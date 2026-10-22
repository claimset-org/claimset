"""Study branches (lab record spec v0.7 §3, §3.4, §4.5, §7.1, rule L20): the record lives on claimset/<study>,
checked out at claimset/<study>/, committed and pushed after every command, never merged with code."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from claimset import cli
from claimset.lab import anchor as anchor_mod
from claimset.lab import guard, record
from claimset.lab.core import Lab, verify

from test_lab_protect import HAVE_GIT, git, make_repo

SCRIPT = "import json, os\njson.dump([{'quantity': 'q', 'value': 1.5}], open(os.environ['CLAIMSET_RESULTS'], 'w'))\n"


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class StudyRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.top = Path(self.tmp.name).resolve() / "pkg"
        self.top.mkdir()
        self.remote = make_repo(self.top)
        (self.top / "R").mkdir()
        (self.top / "R" / "f.R").write_text("f <- function() 1\n", encoding="utf-8")
        (self.top / ".Rbuildignore").write_text("^\\.git$\n", encoding="utf-8")
        git("add", "-A", cwd=self.top)
        git("commit", "-qm", "code", cwd=self.top)
        git("push", "-q", "origin", "HEAD:refs/heads/master", cwd=self.top)
        self.old_cwd = os.getcwd()
        self.old_env = {k: os.environ.pop(k, None) for k in ("CLAIMSET_LAB", "CLAIMSET_STUDY", "CLAIMSET_ACTOR")}
        os.chdir(self.top)

    def tearDown(self):
        os.chdir(self.old_cwd)
        for k, v in self.old_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue().strip(), err.getvalue().strip()

    def remote_log(self, study):
        show = subprocess.run(["git", "show", f"refs/heads/claimset/{study}:lab/lab.jsonl"], cwd=self.remote,
                              capture_output=True, text=True)
        return [json.loads(l) for l in show.stdout.splitlines() if l.strip()]

    def new_study(self, name="alpha"):
        code, out, err = self.cli("setup", "--study", name, "--title", "Study " + name)
        self.assertEqual(code, 0, err)
        code, out, err = self.cli("lab", "actor", "--kind", "human", "--name", "R", "--study", name)
        self.assertEqual(code, 0, err)
        return Lab(self.top / "claimset" / name / "lab")


class TestSetup(StudyRepo):
    def test_a_new_study_gets_its_own_branch_and_checkout(self):
        lab = self.new_study()
        self.assertEqual(record.branch_of(self.top / "claimset" / "alpha"), "claimset/alpha")
        self.assertEqual(lab.state().study, "alpha")
        self.assertIn("/claimset/", (self.top / ".gitignore").read_text())
        self.assertIn("^claimset$", (self.top / ".Rbuildignore").read_text())
        self.assertIn("^claimset-code$", (self.top / ".Rbuildignore").read_text())
        # the code checkout does not see the record, and the study branch shares no history with the code
        status = git("status", "--porcelain", cwd=self.top)
        self.assertNotIn("claimset/", status)
        out = subprocess.run(["git", "merge-base", "refs/heads/master", "refs/heads/claimset/alpha"], cwd=self.top,
                             capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stderr), (1, ""))           # no common ancestor, and no error
        # every command that writes is committed and pushed
        self.assertEqual([e["id"] for e in self.remote_log("alpha")], ["INIT", "AC1"])
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        self.assertEqual(self.cli("lab", "question", "Q?")[0], 0)
        self.assertEqual([e["id"] for e in self.remote_log("alpha")], ["INIT", "AC1", "Q1"])
        self.assertTrue(verify(lab)["ok"])
        self.assertEqual(anchor_mod.read_anchors(lab)[-1]["receipt"]["branch"], "claimset/alpha")

    def test_init_in_a_repository_makes_a_study_branch(self):
        code, out, err = self.cli("lab", "init", "--study", "beta", "--title", "B")
        self.assertEqual(code, 0, err)
        self.assertIn("on branch claimset/beta", out)
        self.assertEqual(record.branch_of(self.top / "claimset" / "beta"), "claimset/beta")

    def test_a_fresh_clone_attaches_every_study(self):
        self.new_study("alpha")
        self.new_study("beta")
        clone = Path(self.tmp.name) / "clone"
        git("clone", "-q", str(self.remote), str(clone), cwd=Path(self.tmp.name))
        os.chdir(clone)
        code, out, err = self.cli("setup")
        self.assertEqual(code, 0, err)
        self.assertIn("alpha: attached", out)
        self.assertIn("beta: attached", out)
        self.assertTrue((clone / "claimset" / "beta" / "lab" / "lab.jsonl").exists())
        code, out, _ = self.cli("studies")
        self.assertIn("alpha: 2 events", out)
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        self.assertEqual(self.cli("lab", "question", "Q?", "--study", "beta")[0], 0)
        self.assertEqual([e["id"] for e in self.remote_log("beta")][-1], "Q1")

    def test_ci_workflow(self):
        code, out, _ = self.cli("setup", "--ci")
        self.assertEqual(code, 0)
        text = (self.top / ".github" / "workflows" / "claimset-study-branches.yml").read_text()
        self.assertIn("--first-parent --max-parents=0", text)
        self.assertIn("^claimset/", text)


class TestRuns(StudyRepo):
    def test_the_run_pins_its_code_commit(self):
        lab = self.new_study()
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        git("checkout", "-q", "-b", "experiment", cwd=self.top)
        (self.top / "claimset-code").mkdir()
        (self.top / "claimset-code" / "s.py").write_text(SCRIPT, encoding="utf-8")
        git("add", "-A", cwd=self.top)
        git("commit", "-qm", "study script", cwd=self.top)
        code_commit = git("rev-parse", "HEAD", cwd=self.top)
        code, out, err = self.cli("lab", "run", "--", sys.executable, "claimset-code/s.py")
        self.assertEqual(code, 0, err)
        run = [e for e in lab.read() if e["type"] == "run"][0]
        self.assertEqual(run["body"]["code_version"], code_commit)
        self.assertFalse(run["body"]["dirty"])
        parents = git("rev-list", "--parents", "-n", "1", "refs/heads/claimset/alpha", cwd=self.top).split()[1:]
        self.assertIn(code_commit, parents)
        # the code branch goes away (a squash merge, a deleted branch): the remote still has the commit
        git("checkout", "-q", "master", cwd=self.top)
        git("branch", "-q", "-D", "experiment", cwd=self.top)
        out = subprocess.run(["git", "cat-file", "-e", f"{code_commit}^{{commit}}"], cwd=self.remote)
        self.assertEqual(out.returncode, 0)
        reach = subprocess.run(["git", "merge-base", "--is-ancestor", code_commit, "refs/heads/claimset/alpha"], cwd=self.remote)
        self.assertEqual(reach.returncode, 0)
        self.assertTrue(verify(lab, remote=True)["ok"], verify(lab, remote=True)["errors"])

    def test_runs_start_from_the_code_checkout(self):
        self.new_study()
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        os.chdir(self.top / "claimset" / "alpha")
        code, _, err = self.cli("lab", "run", "--", sys.executable, "-c", "print(1)")
        self.assertEqual(code, 1)
        self.assertIn("[L20]", err)


class TestKeptApart(StudyRepo):
    def test_a_lab_in_the_code_checkout_is_not_written(self):
        lab = Lab.create(self.top / "claimset" / "gamma" / "lab", "G")
        lab.append("actor", {"kind": "human", "name": "R"}, by="AC1")
        code, _, err = self.cli("lab", "question", "Q?", "--study", "gamma", "--by", "AC1")
        self.assertEqual(code, 1)
        self.assertIn("[L20]", err)
        self.assertIn("--adopt", err)
        self.assertEqual(self.cli("lab", "summary", "--study", "gamma")[0], 0)        # reading is fine

    def test_adopting_a_study_from_the_code_branch(self):
        lab = Lab.create(self.top / "claimset" / "gamma" / "lab", "G")
        human = lab.append("actor", {"kind": "human", "name": "R"}, by="AC1")["id"]
        lab.append("question", {"text": "Q?"}, by=human)
        anchor_mod.save_config(lab, {"anchor": {"service": "git_remote", "remote": "origin"}})
        old_anchor = anchor_mod.anchor(lab)                                  # a record anchored the old way
        (self.top / "claimset" / "gamma" / "code").mkdir()
        (self.top / "claimset" / "gamma" / "code" / "proto.py").write_text("print(1)\n", encoding="utf-8")
        (self.top / "claimset" / "gamma" / "gamma.claimset.json").write_text("{}\n", encoding="utf-8")
        git("add", "-f", "-A", cwd=self.top)
        git("commit", "-qm", "study on the code branch (the old way)", cwd=self.top)
        code_commit = git("rev-parse", "HEAD", cwd=self.top)
        code, out, err = self.cli("setup", "--study", "gamma", "--adopt")
        self.assertEqual(code, 1)
        self.assertIn("--by", err)
        code, out, err = self.cli("setup", "--study", "gamma", "--adopt", "--by", human)
        self.assertEqual(code, 0, err)
        study = self.top / "claimset" / "gamma"
        self.assertEqual(record.branch_of(study), "claimset/gamma")
        self.assertTrue((study / "gamma.claimset.json").exists())
        self.assertFalse((study / "code").exists())
        self.assertTrue((self.top / "claimset-code" / "gamma" / "proto.py").exists())
        moved = Lab(study / "lab")
        last = moved.read()[-1]
        self.assertEqual(last["type"], "relocation")
        self.assertEqual(last["body"]["branch"], "claimset/gamma")
        self.assertEqual(last["body"]["moved"], [{"from": "claimset/gamma/code", "to": "claimset-code/gamma"}])
        reach = subprocess.run(["git", "merge-base", "--is-ancestor", code_commit, "refs/heads/claimset/gamma"], cwd=self.remote)
        self.assertEqual(reach.returncode, 0)
        staged = git("status", "--porcelain", cwd=self.top)
        self.assertIn("D  claimset/gamma/lab/lab.jsonl", staged)
        self.assertIn("R  claimset/gamma/code/proto.py -> claimset-code/gamma/proto.py", staged)
        # the record goes on; the old receipts are checked on their own branch
        self.assertEqual(self.cli("lab", "question", "Q2?", "--study", "gamma", "--by", human)[0], 0)
        rep = verify(moved, remote=True)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertEqual(anchor_mod.read_anchors(moved)[0]["receipt"]["branch"], old_anchor["receipt"]["branch"])

    def test_guard_keeps_study_branches_and_code_apart(self):
        def bash(command):
            return guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.top)})
        for command in ("git merge claimset/alpha", "git pull origin claimset/alpha", "git rebase claimset/alpha",
                        "git checkout claimset/alpha", "git switch origin/claimset/alpha",
                        "git push -f origin claimset/alpha", "git push origin --delete claimset/alpha",
                        "git push origin :claimset/alpha", "git branch -D claimset/alpha",
                        "git add -f claimset/alpha/lab"):
            self.assertIsNotNone(bash(command), command)
        for command in ("git merge feature", "git push origin master", "git checkout -b feature",
                        "git log claimset/alpha", "git diff master claimset/alpha -- lab"):
            self.assertIsNone(bash(command), command)

    def test_session_start_hook_shows_the_studies(self):
        cfg = guard.claude_code_config("claimset lab guard")
        self.assertEqual(cfg["hooks"]["SessionStart"][0]["hooks"][0]["command"], "claimset studies")


if __name__ == "__main__":
    unittest.main()
