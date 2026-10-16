"""Tests for agent logs and merging (lab record spec v0.4 §3.1, §5.16, rule L10)."""

import json
import os
import sys
import unittest
from pathlib import Path

from claimset.lab import anchor as anchor_mod
from claimset.lab.core import LabError, canonical, content_hash, header_hash, verify
from claimset.lab.run import run_command

from test_lab import DECISION, PlanRunCase, TestCli, run_body
from test_lab_protect import HAVE_GIT, make_repo, rewrite_consistently


def append_line(path, event):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(canonical(event).decode("utf-8") + "\n")


def chained(prev, seq, eid, etype, by, body, at="2026-10-15T11:00:00Z", supersedes=None, origin=None):
    """An event correctly chained after `prev`, as a forger with the tool's code would write it."""
    return {"seq": seq, "id": eid, "type": etype, "at": at, "by": by, "prev": header_hash(prev),
            "content_hash": content_hash(body), "supersedes": supersedes, "origin": origin, "body": body}


class AgentCase(PlanRunCase):
    def setUp(self):
        super().setUp()
        self.agent2 = self.lab.append("actor", {"kind": "agent", "name": "Agent B",
                                                "models": [{"provider": "P", "name": "M", "version": "1"}]},
                                      by=self.human)["id"]
        self.a, self.b = self.agent, self.agent2   # AC2, AC3
        self.lab.start_agent(self.a)
        self.lab.start_agent(self.b)

    def write(self, by, etype, body, supersedes=None):
        return self.lab.append(etype, body, by=by, supersedes=supersedes)

    def errors(self, rep=None):
        rep = rep or verify(self.lab)
        return [(e["rule"], e.get("log"), e["message"]) for e in rep["errors"]]

    def assertVerifies(self):
        rep = verify(self.lab)
        self.assertTrue(rep["ok"], rep["errors"])


class TestAgentLogs(AgentCase):
    def test_writes_go_to_the_agent_log_with_prefixed_ids(self):
        main_before = self.lines()
        e = self.write(self.a, "note", {"text": "agent note"})
        self.assertEqual(e["id"], f"{self.a}.NT1")
        self.assertEqual(e["seq"], 1)
        self.assertEqual(e["prev"], header_hash(self.lab.read()[-1]))
        self.assertEqual(self.lines(), main_before)
        self.assertEqual(self.write(self.b, "note", {"text": "other"})["id"], f"{self.b}.NT1")
        self.assertEqual(self.write(self.a, "note", {"text": "again"})["id"], f"{self.a}.NT2")
        self.assertEqual(self.write(self.human, "note", {"text": "main"})["id"], "NT1")
        self.assertVerifies()

    def test_start_needs_registered_actor_and_only_once(self):
        with self.assertRaises(LabError) as cm:
            self.lab.start_agent("AC9")
        self.assertEqual(cm.exception.rule, "L8")
        with self.assertRaises(LabError) as cm:
            self.lab.start_agent(self.a)
        self.assertEqual(cm.exception.rule, "L10")

    def test_agent_sees_base_and_own_events_only(self):
        self.write(self.a, "note", {"text": "first", "refers_to": [self.h]})
        later = self.write(self.human, "note", {"text": "after the agent started"})["id"]
        other = self.write(self.b, "note", {"text": "B"})["id"]
        with self.assertRaises(LabError) as cm:
            self.write(self.a, "note", {"text": "x", "refers_to": [later]})
        self.assertEqual(cm.exception.rule, "L12")
        self.assertIn("after merging", cm.exception.message)
        with self.assertRaises(LabError):
            self.write(self.a, "note", {"text": "x", "refers_to": [other]})
        self.write(self.a, "note", {"text": "own", "refers_to": [f"{self.a}.NT1"]})

    def test_empty_agent_log_starts_from_current_main_head(self):
        later = self.write(self.human, "note", {"text": "before the agent's first event"})["id"]
        self.write(self.a, "note", {"text": "x", "refers_to": [later]})

    def test_main_only_types_refused(self):
        with self.assertRaises(LabError) as cm:
            self.write(self.a, "actor", {"kind": "agent", "name": "C",
                                         "models": [{"provider": "P", "name": "M", "version": "1"}]})
        self.assertEqual(cm.exception.rule, "L10")

    def test_corrections_only_of_own_events(self):
        own = self.write(self.a, "note", {"text": "typo"})["id"]
        fixed = self.write(self.a, "note", {"text": "fixed"}, supersedes=own)
        self.assertEqual(fixed["supersedes"], own)
        with self.assertRaises(LabError) as cm:
            self.write(self.a, "hypothesis", {"statement": "Y", "question": self.q, "origin": "a_priori"},
                       supersedes=self.h)
        self.assertEqual(cm.exception.rule, "L10")

    def test_run_in_agent_log(self):
        out = run_command(self.lab, self.a, [sys.executable, str(self.script)], echo=False, cwd=self.tmp.name)
        rn = out["run"]["id"]
        self.assertEqual(rn, f"{self.a}.RN1")
        self.assertTrue((self.lab.root / "runs" / rn / "out.txt").is_file())
        self.assertEqual(out["results"][0]["id"], f"{self.a}.RS1")
        self.assertVerifies()
        (self.lab.root / "runs" / rn / "out.txt").write_text("changed")
        self.assertIn("L13", [r for r, _, _ in self.errors()])

    def test_forged_agent_event_by_another_actor(self):
        path = self.lab.agent_log_path(self.a)
        append_line(path, chained(self.lab.read()[-1], 1, f"{self.a}.NT1", "note", self.human, {"text": "x"}))
        self.assertIn(("L10", f"agents/{self.a}.jsonl"), [(r, l) for r, l, _ in self.errors()])

    def test_tampered_agent_log_detected(self):
        self.write(self.a, "note", {"text": "original"})
        path = self.lab.agent_log_path(self.a)
        path.write_text(path.read_text().replace("original", "edited"))
        self.assertIn(("L2", f"agents/{self.a}.jsonl"), [(r, l) for r, l, _ in self.errors()])


class TestMerge(AgentCase):
    def test_merge_interleaves_by_time(self):
        self.write(self.b, "note", {"text": "b1"})
        self.write(self.a, "note", {"text": "a1"})
        self.write(self.b, "note", {"text": "b2"})
        n_main = len(self.lab.read())
        mg = self.lab.merge(self.human)
        self.assertEqual(mg["id"], "MG1")
        self.assertEqual(mg["body"], {"agents": [self.a, self.b], "events_merged": 3})
        events = self.lab.read()
        self.assertEqual(events[n_main]["id"], "MG1")
        self.assertEqual([e["id"] for e in events[n_main + 1:]],
                         [f"{self.b}.NT1", f"{self.a}.NT1", f"{self.b}.NT2"])
        first = events[n_main + 1]
        agent_first = self.lab.read_agent(self.b)[0]
        self.assertEqual(first["origin"], {"log": f"agents/{self.b}.jsonl", "seq": 1,
                                           "header_hash": header_hash(agent_first)})
        self.assertEqual(first["body"], agent_first["body"])
        self.assertVerifies()

    def test_ties_broken_by_actor_then_seq(self):
        self.lab.clock = lambda: "2026-10-15T12:00:00Z"
        self.write(self.b, "note", {"text": "b1"})
        self.write(self.a, "note", {"text": "a1"})
        self.write(self.a, "note", {"text": "a2"})
        self.lab.merge(self.human)
        self.assertEqual([e["id"] for e in self.lab.read()[-3:]],
                         [f"{self.a}.NT1", f"{self.a}.NT2", f"{self.b}.NT1"])

    def test_each_agents_order_kept_even_if_clock_goes_back(self):
        times = iter(["2026-10-15T12:00:05Z", "2026-10-15T12:00:01Z"])
        self.lab.clock = lambda: next(times)
        self.write(self.a, "hypothesis", {"statement": "Z", "question": self.q, "origin": "a_priori"})
        self.write(self.a, "note", {"text": "about Z", "refers_to": [f"{self.a}.H1"]})
        self.lab.clock = lambda: "2026-10-15T12:00:09Z"
        self.lab.merge(self.human, [self.a])
        self.assertEqual([e["id"] for e in self.lab.read()[-2:]], [f"{self.a}.H1", f"{self.a}.NT1"])
        self.assertVerifies()

    def test_merge_selected_logs_and_only_once(self):
        self.write(self.a, "note", {"text": "a1"})
        self.write(self.b, "note", {"text": "b1"})
        self.lab.merge(self.human, [self.a])
        with self.assertRaises(LabError) as cm:
            self.lab.merge(self.human, [self.a])
        self.assertEqual(cm.exception.rule, "L10")
        self.assertEqual(self.lab.merge(self.human)["body"]["agents"], [self.b])
        with self.assertRaises(LabError):
            self.lab.merge(self.human)

    def test_after_merge_writes_go_to_main_and_log_cannot_restart(self):
        self.write(self.a, "note", {"text": "a1"})
        self.lab.merge(self.human, [self.a])
        e = self.write(self.a, "note", {"text": "later", "refers_to": [f"{self.a}.NT1"]})
        self.assertEqual(e["id"], "NT1")
        self.assertIsNone(e["origin"])
        with self.assertRaises(LabError) as cm:
            self.lab.start_agent(self.a)
        self.assertEqual(cm.exception.rule, "L10")
        self.assertVerifies()

    def test_conflicting_merge_is_refused_whole(self):
        rn = self.lab.append("run", run_body(), by=self.human)["id"]
        rs = self.lab.append("result", {"run": rn, "quantity": "q", "value": 1, "source": "results.json"},
                             by=self.human)["id"]
        self.lab.agent_log_path(self.a).unlink()          # restart A from the current head
        self.lab.start_agent(self.a)
        self.write(self.a, "note", {"text": "fine"})
        self.write(self.a, "status", {"target": self.h, "to": "supported", "rationale": "r", "evidence": [rs]})
        # Meanwhile the hypothesis is corrected in the main log: it was suggested by that run.
        self.lab.append("hypothesis", {"statement": "X improves Y.", "question": self.q,
                                       "origin": "from_exploration", "prompted_by": [rn]},
                        by=self.human, supersedes=self.h)
        before = self.lines()
        with self.assertRaises(LabError) as cm:
            self.lab.merge(self.human, [self.a])
        self.assertEqual(cm.exception.rule, "L6")
        self.assertIn("nothing was merged", cm.exception.message)
        self.assertEqual(self.lines(), before)

    def test_tampered_agent_log_is_not_merged(self):
        self.write(self.a, "note", {"text": "original"})
        path = self.lab.agent_log_path(self.a)
        path.write_text(path.read_text().replace("original", "edited"))
        before = self.lines()
        with self.assertRaises(LabError) as cm:
            self.lab.merge(self.human, [self.a])
        self.assertEqual(cm.exception.rule, "L10")
        self.assertEqual(self.lines(), before)

    def test_rewritten_merged_event_detected(self):
        self.write(self.a, "note", {"text": "agent wrote this"})
        self.lab.merge(self.human, [self.a])
        rewrite_consistently(self.lab, len(self.lab.read()) - 1, {"text": "someone else wrote this"})
        msgs = self.errors()
        self.assertTrue(any(r == "L10" and "does not match" in m for r, _, m in msgs), msgs)

    def test_agent_log_extended_after_merge_detected(self):
        self.write(self.a, "note", {"text": "a1"})
        self.lab.merge(self.human, [self.a])
        agent = self.lab.read_agent(self.a)
        append_line(self.lab.agent_log_path(self.a),
                    chained(agent[-1], 2, f"{self.a}.NT2", "note", self.a, {"text": "late"}))
        self.assertTrue(any("after it was merged" in m for _, _, m in self.errors()))

    def test_missing_merged_log_detected(self):
        self.write(self.a, "note", {"text": "a1"})
        self.lab.merge(self.human, [self.a])
        self.lab.agent_log_path(self.a).unlink()
        self.assertTrue(any("is missing" in m for _, _, m in self.errors()))

    def test_prefixed_id_without_origin_in_main_detected(self):
        main = self.lab.read()
        append_line(self.lab.log_path, chained(main[-1], len(main) + 1, f"{self.a}.NT1", "note", self.a,
                                               {"text": "smuggled"}))
        self.assertTrue(any(r == "L10" and "no origin" in m for r, _, m in self.errors()))

    def test_runs_survive_merge(self):
        run_command(self.lab, self.a, [sys.executable, str(self.script)], echo=False, cwd=self.tmp.name)
        self.lab.merge(self.human)
        self.assertVerifies()
        run_ev = self.lab.state().get(f"{self.a}.RN1")
        self.assertEqual(run_ev["origin"]["log"], f"agents/{self.a}.jsonl")


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TestAgentAnchoring(AgentCase):
    def setUp(self):
        super().setUp()
        make_repo(self.tmp.name)
        anchor_mod.save_config(self.lab, {"anchor": {"service": "git_remote", "remote": "origin",
                                                     "branch": "claimset-lab", "every": "plan"}})

    def agent_plan(self):
        return self.write(self.a, "plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "d",
                                           "analysis": "a", "decision": DECISION})

    def test_confirmatory_run_from_agent_plan(self):
        pl = self.agent_plan()
        self.assertFalse(anchor_mod.covered(self.lab, pl, self.a))
        out = run_command(self.lab, self.a, [sys.executable, str(self.script)], plan=pl["id"], echo=False,
                          cwd=self.tmp.name)
        self.assertEqual(out["run"]["body"]["label"], "confirmatory")
        entry = anchor_mod.read_anchors(self.lab)[-1]
        self.assertEqual(entry["agents"][self.a]["seq"], pl["seq"])
        self.assertTrue(anchor_mod.covered(self.lab, pl, self.a))
        self.assertVerifies()
        self.lab.merge(self.human)
        self.assertVerifies()
        rep = verify(self.lab, remote=True)
        self.assertTrue(rep["ok"], rep["errors"])

    def test_rewritten_agent_log_detected_by_anchor(self):
        self.write(self.a, "note", {"text": "original"})
        anchor_mod.anchor(self.lab)
        path = self.lab.agent_log_path(self.a)
        events = self.lab.read_agent(self.a)
        events[0]["body"] = {"text": "rewritten"}
        events[0]["content_hash"] = content_hash(events[0]["body"])
        path.write_text(canonical(events[0]).decode("utf-8") + "\n")
        self.assertIn(("L14", f"agents/{self.a}.jsonl"), [(r, l) for r, l, _ in self.errors()])
        with self.assertRaises(LabError) as cm:
            anchor_mod.anchor(self.lab)
        self.assertEqual(cm.exception.rule, "L14")


class TestMalformedAndForged(AgentCase):
    """Forged or malformed logs are reported by verify, never crash it, and are never merged."""

    def forge_agent(self, actor, events):
        path = self.lab.agent_log_path(actor)
        path.write_text("".join(canonical(e).decode("utf-8") + "\n" for e in events))

    def test_withheld_event_still_follows_agent_log_rules(self):
        body = {"withheld": True, "reason": "other"}
        e = chained(self.lab.read()[-1], 1, f"{self.a}.NT1", "note", self.b, body)
        e["content_hash"] = content_hash({"text": "hidden"})
        self.forge_agent(self.a, [e])
        self.assertIn("L10", [r for r, _, _ in self.errors()])

    def test_withheld_merge_event_is_an_error_and_log_stays_merged(self):
        self.write(self.a, "note", {"text": "a1"})
        self.lab.merge(self.human, [self.a])
        events = self.lab.read()
        mg = next(i for i, e in enumerate(events) if e["type"] == "merge")
        events[mg]["body"] = {"withheld": True, "reason": "other"}
        self.lab.log_path.write_text("".join(canonical(e).decode("utf-8") + "\n" for e in events))
        self.assertTrue(any("never withheld" in m for _, _, m in self.errors()))
        with self.assertRaises(LabError) as cm:
            self.lab.merge(self.human, [self.a])
        self.assertEqual(cm.exception.rule, "L10")

    def test_malformed_time_is_reported_and_not_merged(self):
        self.forge_agent(self.a, [chained(self.lab.read()[-1], 1, f"{self.a}.NT1", "note", self.a,
                                          {"text": "x"}, at=None)])
        self.write(self.b, "note", {"text": "b"})
        self.assertIn(("L2", f"agents/{self.a}.jsonl"), [(r, l) for r, l, _ in self.errors()])
        with self.assertRaises(LabError) as cm:
            self.lab.merge(self.human)
        self.assertEqual(cm.exception.rule, "L10")

    def test_reordered_merged_events_detected(self):
        self.write(self.a, "note", {"text": "a1"})
        self.write(self.a, "note", {"text": "a2"})
        self.lab.merge(self.human, [self.a])
        events = self.lab.read()
        events[-1], events[-2] = events[-2], events[-1]
        for i in (-2, -1):
            events[i]["seq"] = len(events) + i + 1
            events[i]["prev"] = header_hash(events[i - 1])
        self.lab.log_path.write_text("".join(canonical(e).decode("utf-8") + "\n" for e in events))
        self.assertTrue(any("own order" in m for _, _, m in self.errors()))

    def test_malformed_lines_do_not_crash_verify(self):
        self.write(self.a, "note", {"text": "a1"})
        with open(self.lab.agent_log_path(self.a), "a") as fh:
            fh.write('{"seq": 2}\n[1, 2]\n')
        with open(self.lab.log_path, "a") as fh:
            fh.write('{"seq": 99}\n')
        rep = verify(self.lab)
        self.assertFalse(rep["ok"])
        self.assertIn("L2", [e["rule"] for e in rep["errors"]])

    def test_malformed_anchor_entries_do_not_crash_verify(self):
        (self.lab.root / "anchors.jsonl").write_text('{"seq": null, "agents": {"AC2": "bad"}}\n"x"\n')
        anchor_mod.save_config(self.lab, {"anchor": {"service": "git_remote"}})
        self.plan_id = self.lab.append("plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "d",
                                                "analysis": "a", "decision": DECISION}, by=self.human)["id"]
        rules = [r for r, _, _ in self.errors(verify(self.lab, check_files=False))]
        self.assertIn("L14", rules)

    def test_log_of_unregistered_actor_reported(self):
        (self.lab.root / "agents" / "AC9.jsonl").write_text("")
        self.assertTrue(any("not a registered actor" in m for _, _, m in self.errors()))


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TestWorkingCopies(unittest.TestCase):
    """An agent working in its own clone anchors and later merges (§3.1, §7.1)."""

    def setUp(self):
        import subprocess
        import tempfile
        from claimset.lab.core import Lab
        from test_lab import ticking_clock

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.git = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                                                  text=True).stdout.strip()
        self.A = root / "A"
        self.A.mkdir()
        remote = make_repo(self.A)
        self.labA = Lab.create(self.A / "lab", "t", clock=ticking_clock())
        self.hum = self.labA.append("actor", {"kind": "human", "name": "H"}, by="AC1")["id"]
        self.ag = self.labA.append("actor", {"kind": "agent", "name": "A", "models": [
            {"provider": "p", "name": "n", "version": "1"}]}, by=self.hum)["id"]
        q = self.labA.append("question", {"text": "q"}, by=self.hum)["id"]
        self.h = self.labA.append("hypothesis", {"statement": "s", "question": q, "origin": "a_priori"},
                                  by=self.hum)["id"]
        anchor_mod.save_config(self.labA, {"anchor": {"service": "git_remote", "remote": "origin",
                                                      "branch": "claimset-lab"}})
        self.git("add", "-A", cwd=self.A)
        self.git("commit", "-qm", "x", cwd=self.A)
        self.git("push", "-q", "origin", "HEAD:main", cwd=self.A)
        self.B = root / "B"
        self.git("clone", "-q", "-b", "main", str(remote), str(self.B), cwd=root)
        self.git("config", "user.email", "t@example.org", cwd=self.B)
        self.git("config", "user.name", "T", cwd=self.B)
        self.labB = Lab(self.B / "lab", clock=ticking_clock())

    def tearDown(self):
        self.tmp.cleanup()

    def test_agent_copy_anchors_and_merges(self):
        self.labB.start_agent(self.ag)
        self.labA.append("note", {"text": "main moves on"}, by=self.hum)
        anchor_mod.anchor(self.labA)
        pl = self.labB.append("plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "d",
                                       "analysis": "a", "decision": DECISION}, by=self.ag)
        anchor_mod.ensure_covered(self.labB, pl, self.ag)          # B is behind on lab.jsonl: allowed
        rep = verify(self.labB, remote=True)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertTrue(any("behind" in n for n in rep["notes"]))
        anchor_mod.anchor(self.labA)                               # A keeps B's agent log on the branch
        branch_files = self.git("ls-tree", "-r", "--name-only", "claimset-lab", cwd=self.A)
        self.assertIn("lab/agents/AC2.jsonl", branch_files)
        # Bring B's work into A, as a git merge with `merge=union` for anchors.jsonl would.
        (self.A / "lab" / "agents" / "AC2.jsonl").write_bytes((self.B / "lab" / "agents" / "AC2.jsonl").read_bytes())
        a_lines = (self.A / "lab" / "anchors.jsonl").read_text().splitlines()
        b_lines = (self.B / "lab" / "anchors.jsonl").read_text().splitlines()
        (self.A / "lab" / "anchors.jsonl").write_text("\n".join(a_lines + [l for l in b_lines if l not in a_lines]) + "\n")
        self.labA.merge(self.hum)
        anchor_mod.anchor(self.labA)
        rep = verify(self.labA, remote=True)
        self.assertTrue(rep["ok"], rep["errors"])

    def test_rewritten_branch_detected(self):
        anchor_mod.anchor(self.labA)
        first = anchor_mod.read_anchors(self.labA)[0]["receipt"]["commit"]
        self.labA.append("note", {"text": "more"}, by=self.hum)
        anchor_mod.anchor(self.labA)
        orphan = self.git("commit-tree", self.git("rev-parse", first + "^{tree}", cwd=self.A), "-m", "x", cwd=self.A)
        self.git("push", "-q", "-f", "origin", f"{orphan}:refs/heads/claimset-lab", cwd=self.A)
        rep = verify(self.labA, remote=True)
        self.assertTrue(any("not on" in e["message"] for e in rep["errors"]), rep["errors"])


class TestAgentCli(TestCli):
    def test_two_agents_session(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        for name in ("A", "B"):
            self.run_cli("lab", "actor", "--kind", "agent", "--name", name, "--model", "P|M|1", "--by", "AC1")
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        self.run_cli("lab", "question", "Q?")
        code, out, err = self.run_cli("lab", "start", "--by", "AC2")
        self.assertEqual(code, 0, err)
        self.assertIn("agents/AC2.jsonl", out)
        self.assertEqual(self.run_cli("lab", "start", "--by", "AC3")[0], 0)
        self.assertEqual(self.run_cli("lab", "hypothesis", "H.", "--question", "Q1", "--by", "AC2")[1], "AC2.H1")
        self.assertEqual(self.run_cli("lab", "note", "n", "--by", "AC3")[1], "AC3.NT1")
        code, out, _ = self.run_cli("lab", "summary", "--by", "AC2")
        self.assertIn("Agent log of AC2", out)
        self.assertIn("AC2.H1", out)
        code, out, _ = self.run_cli("lab", "summary", "--json")
        self.assertEqual(json.loads(out)["agent_logs"]["AC3"], {"events": 1, "merged_by": None})
        code, out, _ = self.run_cli("lab", "list", "--by", "AC3")
        self.assertIn("AC3.NT1", out)
        self.assertNotIn("AC2.H1", out)
        code, out, err = self.run_cli("lab", "merge")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "MG1: merged 2 events from AC2, AC3")
        code, out, _ = self.run_cli("lab", "show", "AC2.H1")
        self.assertEqual(json.loads(out)["event"]["origin"]["log"], "agents/AC2.jsonl")
        code, out, _ = self.run_cli("lab", "verify")
        self.assertEqual(code, 0, out)
        self.assertIn("agent log AC2: 1 events, merged by MG1", out)


if __name__ == "__main__":
    unittest.main()
