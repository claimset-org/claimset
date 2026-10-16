"""Tests for protecting the lab record: anchoring (§7.1, L14), the guard (§4.5) and the append-only attribute."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from claimset.lab import anchor as anchor_mod
from claimset.lab import guard, protect
from claimset.lab.core import LabError, canonical, content_hash, header_hash, verify
from claimset.lab.run import run_command

from test_lab import PlanRunCase, TestCli

HAVE_GIT = shutil.which("git") is not None


def git(*args, cwd):
    return subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_repo(top):
    """A git repository at `top` with a bare remote `origin`."""
    remote = Path(top).parent / (Path(top).name + "-remote.git")
    git("init", "-q", "--bare", str(remote), cwd=Path(top).parent)
    git("init", "-q", cwd=top)
    git("config", "user.email", "t@example.org", cwd=top)
    git("config", "user.name", "T", cwd=top)
    git("remote", "add", "origin", str(remote), cwd=top)
    return remote


def rewrite_consistently(lab, index, new_body):
    """Replace the body of one event and recompute every hash after it, as a careful forger would."""
    events = lab.read()
    events[index]["body"] = new_body
    prev = events[index - 1]
    for e in events[index:]:
        e["prev"] = header_hash(prev)
        e["content_hash"] = content_hash(e["body"])
        prev = e
    lab.log_path.write_text("".join(canonical(e).decode("utf-8") + "\n" for e in events), encoding="utf-8")


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TestGitAnchoring(PlanRunCase):
    def setUp(self):
        super().setUp()
        self.remote = make_repo(self.tmp.name)
        anchor_mod.save_config(self.lab, {"anchor": {"service": "git_remote", "remote": "origin",
                                                     "branch": "claimset-lab", "every": "plan"}})

    def test_anchor_pushes_and_records_receipt(self):
        first = anchor_mod.anchor(self.lab)
        self.assertEqual(first["seq"], len(self.lab.read()))
        self.assertEqual(git("rev-parse", "refs/heads/claimset-lab", cwd=self.remote), first["receipt"]["commit"])
        self.add("note", {"text": "more"})
        second = anchor_mod.anchor(self.lab)
        parent = git("rev-parse", second["receipt"]["commit"] + "^", cwd=self.remote)
        self.assertEqual(parent, first["receipt"]["commit"])
        self.assertEqual(len(anchor_mod.read_anchors(self.lab)), 2)
        rep = verify(self.lab, remote=True)
        self.assertTrue(rep["ok"], rep["errors"])

    def test_anchor_does_not_touch_working_tree_or_current_branch(self):
        anchor_mod.anchor(self.lab)
        self.assertEqual(git("status", "--porcelain", "--", "lab/lab.jsonl", cwd=self.tmp.name), "?? lab/lab.jsonl")
        self.assertNotEqual(git("symbolic-ref", "--short", "HEAD", cwd=self.tmp.name), "claimset-lab")

    def test_consistent_rewrite_is_detected(self):
        anchor_mod.anchor(self.lab)
        rewrite_consistently(self.lab, 3, {"text": "Does Z help?"})
        rep = verify(self.lab, check_files=False)
        self.assertIn("L14", [e["rule"] for e in rep["errors"]])
        rep = verify(self.lab, check_files=False, remote=True)
        self.assertTrue(any("rewritten" in e["message"] for e in rep["errors"] if e["rule"] == "L14"))

    def test_rewrite_also_of_anchors_file_is_caught_by_remote(self):
        anchor_mod.anchor(self.lab)
        rewrite_consistently(self.lab, 3, {"text": "Does Z help?"})
        (self.lab.root / anchor_mod.ANCHORS_FILE).write_text("")
        self.assertTrue(verify(self.lab, check_files=False)["ok"])
        self.assertFalse(verify(self.lab, check_files=False, remote=True)["ok"])

    def test_anchoring_refused_after_rewrite(self):
        anchor_mod.anchor(self.lab)
        rewrite_consistently(self.lab, 3, {"text": "Does Z help?"})
        with self.assertRaises(LabError) as cm:
            anchor_mod.anchor(self.lab)
        self.assertEqual(cm.exception.rule, "L14")

    def test_anchoring_refused_after_truncation(self):
        self.add("note", {"text": "more"})
        anchor_mod.anchor(self.lab)
        lines = self.lines()
        self.write_lines(lines[:-1])
        with self.assertRaises(LabError) as cm:
            anchor_mod.anchor(self.lab)
        self.assertIn("removed", cm.exception.message)

    def test_confirmatory_run_anchors_its_plan_first(self):
        pl = self.plan()
        self.assertFalse(anchor_mod.covered(self.lab, self.lab.state().get(pl)))
        out = self.execute(plan=pl)
        self.assertEqual(out["run"]["body"]["label"], "confirmatory")
        self.assertTrue(anchor_mod.covered(self.lab, self.lab.state().get(pl)))
        rep = verify(self.lab)
        self.assertTrue(rep["ok"], rep["errors"])

    def test_confirmatory_run_refused_when_anchoring_fails(self):
        pl = self.plan()
        git("remote", "set-url", "origin", str(Path(self.tmp.name) / "missing.git"), cwd=self.tmp.name)
        with self.assertRaises(LabError) as cm:
            self.execute(plan=pl)
        self.assertEqual(cm.exception.rule, "L5")
        self.assertFalse((self.lab.root / "runs").exists() and any((self.lab.root / "runs").iterdir()))
        out = self.execute(plan=pl, exploratory=True)
        self.assertEqual(out["run"]["body"]["label"], "exploratory")

    def test_run_anchored_after_it_started_breaks_l5(self):
        pl = self.plan()
        self.lab.append("run", {"plan": pl, "label": "confirmatory", "command": ["x"], "exit_status": "success",
                                "outputs": [], "started_at": "2026-10-15T10:00:00Z"}, by=self.agent)
        anchor_mod.anchor(self.lab)
        rep = verify(self.lab, check_files=False)
        self.assertIn("L5", [e["rule"] for e in rep["errors"]])


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TestAnchorCli(TestCli):
    def setUp(self):
        super().setUp()
        make_repo(self.tmp.name)

    def test_plan_is_anchored_automatically(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        code, out, _ = self.run_cli("lab", "anchor", "--git")
        self.assertEqual(code, 0, out)
        self.assertIn("anchored seq 2", out)
        self.run_cli("lab", "question", "Q?")
        self.run_cli("lab", "hypothesis", "H.", "--question", "Q1")
        code, _, err = self.run_cli("lab", "plan", "--kind", "confirmatory", "--hypotheses", "H1",
                                    "--design", "d", "--analysis", "a", "--criterion", "c")
        self.assertEqual(code, 0, err)
        self.assertIn("anchored seq 5", err)
        code, out, _ = self.run_cli("lab", "verify", "--remote")
        self.assertEqual(code, 0, out)


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        (Path(self.cwd) / "lab").mkdir()
        self.old = os.environ.pop("CLAIMSET_LAB", None)

    def tearDown(self):
        if self.old is not None:
            os.environ["CLAIMSET_LAB"] = self.old
        else:
            os.environ.pop("CLAIMSET_LAB", None)
        self.tmp.cleanup()

    def decide(self, tool, **inp):
        return guard.decide({"tool_name": tool, "tool_input": inp, "cwd": self.cwd})

    def bash(self, command):
        return self.decide("Bash", command=command)

    def test_file_tools_blocked_inside_lab(self):
        self.assertIsNotNone(self.decide("Edit", file_path="lab/lab.jsonl"))
        self.assertIsNotNone(self.decide("Write", file_path=str(Path(self.cwd) / "lab" / "runs" / "RN1" / "x")))
        self.assertIsNotNone(self.decide("NotebookEdit", notebook_path="lab/../lab/n.ipynb"))
        self.assertIsNone(self.decide("Write", file_path="analysis.py"))
        self.assertIsNone(self.decide("Edit", file_path="laboratory/notes.md"))

    def test_claimset_lab_commands_allowed(self):
        for cmd in ['claimset lab note "rm -rf lab/ was a bad idea > yes"',
                    'CLAIMSET_ACTOR=AC2 claimset lab status H1 rejected --why "sed -i lab"',
                    "claimset lab run --plan PL1 -- python3 exp.py"]:
            self.assertIsNone(self.bash(cmd), cmd)

    def test_wrapped_and_chained_commands_are_checked(self):
        self.assertIsNotNone(self.bash("claimset lab run -- rm lab/lab.jsonl"))
        self.assertIsNotNone(self.bash("claimset lab note x; rm lab/lab.jsonl"))
        self.assertIsNotNone(self.bash("claimset lab list > lab/lab.jsonl"))

    def test_destructive_shell_commands_blocked(self):
        for cmd in ["rm lab/lab.jsonl", "rm -rf ./lab", f"mv {self.cwd}/lab/lab.jsonl /tmp/x",
                    "sed -i 's/a/b/' lab/lab.jsonl", "truncate -s 0 lab/lab.jsonl",
                    "echo '{}' >> lab/lab.jsonl", "cat x > lab/anchors.jsonl", "git checkout -- lab",
                    "git restore lab/lab.jsonl", "chattr -a lab/lab.jsonl",
                    "python3 -c \"open('lab/lab.jsonl','w')\""]:
            self.assertIsNotNone(self.bash(cmd), cmd)

    def test_reading_allowed(self):
        for cmd in ["cat lab/lab.jsonl", "tail -n 5 lab/lab.jsonl | jq .", "grep H1 lab/lab.jsonl > hits.txt",
                    "ls lab/runs", "git log -- lab", "rm results/old.csv", "python3 exp.py > out.txt"]:
            self.assertIsNone(self.bash(cmd), cmd)

    def test_force_push_of_anchor_branch_blocked(self):
        self.assertIsNotNone(self.bash("git push --force origin claimset-lab"))
        self.assertIsNotNone(self.bash("git push origin +claimset-lab"))
        self.assertIsNotNone(self.bash("git push origin --delete claimset-lab"))
        self.assertIsNotNone(self.bash("git push --mirror origin"))
        self.assertIsNone(self.bash("git push origin main"))
        self.assertIsNone(self.bash("git push -f origin feature"))

    def test_configured_branch_and_lab_dir(self):
        (Path(self.cwd) / "lab" / "config.json").write_text(json.dumps({"anchor": {"branch": "proof"}}))
        self.assertIsNotNone(self.bash("git push -f origin proof"))
        os.environ["CLAIMSET_LAB"] = "study/log"
        self.assertIsNotNone(self.bash("rm study/log/lab.jsonl"))
        self.assertIsNone(self.bash("rm lab/lab.jsonl"))

    def test_hook_exit_codes(self):
        err = io.StringIO()
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "lab/lab.jsonl"}, "cwd": self.cwd})
        self.assertEqual(guard.main_hook(io.StringIO(payload), err), 2)
        self.assertIn("append-only", err.getvalue())
        payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "lab/lab.jsonl"}, "cwd": self.cwd})
        self.assertEqual(guard.main_hook(io.StringIO(payload), io.StringIO()), 0)

    def test_hook_runs_as_command(self):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm lab/lab.jsonl"}, "cwd": self.cwd})
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        out = subprocess.run([sys.executable, "-m", "claimset", "lab", "guard"], input=payload, text=True,
                             capture_output=True, env=env)
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("claimset guard: blocked", out.stderr)

    def test_config_snippet(self):
        hooks = guard.claude_code_config()["hooks"]["PreToolUse"][0]
        self.assertIn("Bash", hooks["matcher"])
        self.assertEqual(hooks["hooks"][0]["command"], "claimset lab guard")


class TestProtect(PlanRunCase):
    def fake_chattr(self, code):
        path = Path(self.tmp.name) / "fake-chattr"
        log = Path(self.tmp.name) / "chattr.log"
        path.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\necho 'Operation not permitted' >&2\nexit {code}\n")
        path.chmod(0o755)
        return str(path), log

    @unittest.skipUnless(sys.platform.startswith("linux"), "chattr is Linux only")
    def test_sets_flag_on_logs(self):
        chattr, log = self.fake_chattr(0)
        out = protect.protect(self.lab, chattr=chattr)
        self.assertTrue(out["ok"], out)
        args = log.read_text().split()
        self.assertEqual(args[0], "+a")
        self.assertIn(str(self.lab.log_path), args)
        self.assertIn(str(self.lab.root / "anchors.jsonl"), args)

    @unittest.skipUnless(sys.platform.startswith("linux"), "chattr is Linux only")
    def test_failure_explains_sudo(self):
        chattr, _ = self.fake_chattr(1)
        out = protect.protect(self.lab, undo=True, chattr=chattr)
        self.assertFalse(out["ok"])
        self.assertTrue(any("sudo chattr -a" in m for m in out["messages"]))

    def test_missing_chattr(self):
        out = protect.protect(self.lab, chattr=str(Path(self.tmp.name) / "no-such-chattr"))
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
