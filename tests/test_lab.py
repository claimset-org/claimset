"""Tests for the lab record core (lab record spec v0.4)."""

import contextlib
import io
import itertools
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from claimset import cli
from claimset.lab import core
from claimset.lab.core import GENESIS_PREV, Lab, LabError, header_hash, verify
from claimset.lab.summary import build


def run_body(**extra):
    body = {"label": "exploratory", "command": ["python3", "x.py"], "exit_status": "success",
            "outputs": [{"path": "results.json", "hash": "sha256:" + "0" * 64}]}
    body.update(extra)
    return body


def ticking_clock():
    counter = itertools.count()
    return lambda: f"2026-10-15T10:{next(counter):02d}:00Z"


class LabCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "lab"
        self.lab = Lab.create(self.dir, "Test study", clock=ticking_clock())
        self.human = self.lab.append("actor", {"kind": "human", "name": "A. Researcher",
                                               "orcid": "0000-0000-0000-0000"}, by="AC1")["id"]
        self.agent = self.lab.append("actor", {"kind": "agent", "name": "Agent",
                                               "models": [{"provider": "P", "name": "M", "version": "1"}]},
                                     by=self.human)["id"]
        self.q = self.add("question", {"text": "Does X help?"})

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, etype, body, by=None, supersedes=None):
        return self.lab.append(etype, body, by=by or self.agent, supersedes=supersedes)["id"]

    def hyp(self, statement="X improves Y.", **extra):
        body = {"statement": statement, "question": self.q, "origin": "a_priori"}
        body.update(extra)
        return self.add("hypothesis", body)

    def assertRefused(self, rule, etype, body, by=None, supersedes=None):
        with self.assertRaises(LabError) as cm:
            self.lab.append(etype, body, by=by or self.agent, supersedes=supersedes)
        self.assertEqual(cm.exception.rule, rule, msg=cm.exception.message)

    def lines(self):
        return self.lab.log_path.read_text(encoding="utf-8").splitlines()

    def write_lines(self, lines):
        self.lab.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestChain(LabCase):
    def test_new_lab_verifies(self):
        rep = verify(self.lab)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertEqual(rep["events"], 4)

    def test_genesis_and_links(self):
        events = self.lab.read()
        self.assertEqual(events[0]["id"], "INIT")
        self.assertIsNone(events[0]["by"])
        self.assertEqual(events[0]["prev"], GENESIS_PREV)
        for a, b in zip(events, events[1:]):
            self.assertEqual(b["prev"], header_hash(a))

    def test_ids_are_numbered_per_prefix(self):
        self.assertEqual([self.hyp(), self.hyp()], ["H1", "H2"])
        self.assertEqual(self.add("note", {"text": "n"}), "NT1")

    def test_cannot_init_twice(self):
        with self.assertRaises(LabError):
            Lab.create(self.dir, "Again")

    def test_changed_body_is_detected(self):
        self.hyp("Original statement.")
        lines = self.lines()
        lines[-1] = lines[-1].replace("Original statement.", "Edited statement.")
        self.write_lines(lines)
        rep = verify(self.lab)
        self.assertFalse(rep["ok"])
        self.assertIn("L2", {e["rule"] for e in rep["errors"]})

    def test_removed_event_is_detected(self):
        self.hyp()
        self.hyp()
        lines = self.lines()
        del lines[4]
        self.write_lines(lines)
        msgs = " ".join(e["message"] for e in verify(self.lab)["errors"])
        self.assertIn("seq", msgs)
        self.assertIn("chain is broken", msgs)

    def test_reordered_events_are_detected(self):
        self.hyp()
        self.add("note", {"text": "n"})
        lines = self.lines()
        lines[-1], lines[-2] = lines[-2], lines[-1]
        self.write_lines(lines)
        self.assertFalse(verify(self.lab)["ok"])

    def test_withheld_body_keeps_chain(self):
        nid = self.add("note", {"text": "names the retailer"})
        lines = self.lines()
        ev = json.loads(lines[-1])
        ev["body"] = {"withheld": True, "reason": "commercial", "comment": "supplier name"}
        lines[-1] = json.dumps(ev)
        self.write_lines(lines)
        rep = verify(self.lab)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertEqual(rep["withheld"], 1)
        self.assertEqual(nid, "NT1")

    def test_withheld_body_needs_reason(self):
        self.add("note", {"text": "x"})
        lines = self.lines()
        ev = json.loads(lines[-1])
        ev["body"] = {"withheld": True}
        lines[-1] = json.dumps(ev)
        self.write_lines(lines)
        self.assertFalse(verify(self.lab)["ok"])

    def test_wrong_prefix_is_detected(self):
        self.hyp()
        lines = self.lines()
        ev = json.loads(lines[-1])
        ev["id"] = "Q9"
        lines[-1] = json.dumps(ev)
        self.write_lines(lines)
        self.assertIn("L12", {e["rule"] for e in verify(self.lab)["errors"]})


class TestRules(LabCase):
    def test_l8_unknown_actor(self):
        self.assertRefused("L8", "note", {"text": "x"}, by="AC9")

    def test_l8_actor_may_register_itself(self):
        self.assertEqual(self.lab.append("actor", {"kind": "human", "name": "B"}, by="AC3")["id"], "AC3")

    def test_agent_needs_model(self):
        self.assertRefused("schema", "actor", {"kind": "agent", "name": "No model"})

    def test_l12_reference_must_exist(self):
        self.assertRefused("L12", "hypothesis", {"statement": "s", "question": "Q9", "origin": "a_priori"})

    def test_reference_type(self):
        nid = self.add("note", {"text": "n"})
        self.assertRefused("schema", "hypothesis", {"statement": "s", "question": nid, "origin": "a_priori"})

    def test_from_exploration_needs_prompt(self):
        self.assertRefused("schema", "hypothesis",
                           {"statement": "s", "question": self.q, "origin": "from_exploration"})

    def test_l7_status_needs_rationale(self):
        h = self.hyp()
        self.assertRefused("L7", "status", {"target": h, "to": "testing"})

    def test_l7_conclusion_needs_evidence(self):
        h = self.hyp()
        self.assertRefused("L7", "status", {"target": h, "to": "supported", "rationale": "looks right"})

    def test_l7_revised_needs_new_hypothesis(self):
        h = self.hyp()
        self.assertRefused("schema", "status", {"target": h, "to": "revised", "rationale": "narrower"})

    def test_l7_decision_needs_text(self):
        self.assertRefused("L7", "decision", {"text": "  "})

    def test_l6_prompting_evidence_cannot_confirm(self):
        n = self.add("note", {"text": "pilot pattern"})
        h = self.hyp(origin="from_exploration", prompted_by=[n])
        self.assertRefused("L6", "status", {"target": h, "to": "supported", "rationale": "r", "evidence": [n]})

    def test_l6_results_of_prompting_run_cannot_confirm(self):
        run = self.add("run", run_body())
        res = self.add("result", {"run": run, "quantity": "q", "value": 1.0, "source": "results.json"})
        h = self.hyp(origin="from_exploration", prompted_by=[run])
        self.assertRefused("L6", "status", {"target": h, "to": "supported", "rationale": "r", "evidence": [res]})

    def test_l6_new_evidence_can_confirm(self):
        n = self.add("note", {"text": "pilot"})
        h = self.hyp(origin="from_exploration", prompted_by=[n])
        run = self.add("run", run_body())
        self.add("status", {"target": h, "to": "supported", "rationale": "new run", "evidence": [run]})

    def test_l6_not_supported_may_use_prompting_evidence(self):
        n = self.add("note", {"text": "pilot"})
        h = self.hyp(origin="from_exploration", prompted_by=[n])
        self.add("status", {"target": h, "to": "not_supported", "rationale": "r", "evidence": [n]})

    def test_l9_no_self_check(self):
        h = self.hyp()
        self.assertRefused("L9", "check", {"target": h, "outcome": "confirmed", "extent": "full"})

    def test_l9_applies_to_correction_chain(self):
        h = self.hyp()
        self.add("hypothesis", {"statement": "Fixed.", "question": self.q, "origin": "a_priori"},
                 by=self.human, supersedes=h)
        self.assertRefused("L9", "check", {"target": h, "outcome": "confirmed", "extent": "full"},
                           by=self.human)

    def test_independent_check(self):
        h = self.hyp()
        self.add("check", {"target": h, "outcome": "confirmed", "extent": "full"}, by=self.human)

    def test_disputed_check_needs_notes(self):
        h = self.hyp()
        self.assertRefused("schema", "check", {"target": h, "outcome": "disputed", "extent": "full"},
                           by=self.human)

    def test_l11_same_type(self):
        h = self.hyp()
        self.assertRefused("L11", "note", {"text": "x"}, supersedes=h)

    def test_l11_only_once(self):
        h = self.hyp()
        self.add("hypothesis", {"statement": "v2", "question": self.q, "origin": "a_priori"}, supersedes=h)
        self.assertRefused("L11", "hypothesis", {"statement": "v3", "question": self.q, "origin": "a_priori"},
                           supersedes=h)

    def test_axiom_untestable(self):
        self.assertRefused("schema", "assumption",
                           {"statement": "a", "category": "axiom", "check": "unchecked"})

    def test_tested_assumption_needs_evidence(self):
        self.assertRefused("schema", "assumption",
                           {"statement": "a", "category": "statistical", "check": "tested"})

    def test_rules_rechecked_by_verify(self):
        """An event that bypassed the tool is caught by verify."""
        n = self.add("note", {"text": "pilot"})
        h = self.hyp(origin="from_exploration", prompted_by=[n])
        st = self.lab.state()
        body = {"target": h, "to": "supported", "rationale": "r", "evidence": [n]}
        head = st.head()
        ev = {"seq": len(st.events) + 1, "id": "ST1", "type": "status", "at": "2026-10-15T11:00:00Z",
              "by": self.agent, "prev": header_hash(head), "content_hash": core.content_hash(body),
              "supersedes": None, "origin": None, "body": body}
        with open(self.lab.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")
        rep = verify(self.lab)
        self.assertEqual([e["rule"] for e in rep["errors"]], ["L6"])


class TestLock(LabCase):
    def test_held_lock_times_out(self):
        self.lab.lock_path.write_text("other")
        old = core.LOCK_TIMEOUT_SECONDS
        core.LOCK_TIMEOUT_SECONDS = 0.1
        try:
            with self.assertRaises(LabError) as cm:
                self.add("note", {"text": "x"})
            self.assertEqual(cm.exception.rule, "lock")
        finally:
            core.LOCK_TIMEOUT_SECONDS = old
            self.lab.lock_path.unlink()

    def test_stale_lock_is_cleared(self):
        self.lab.lock_path.write_text("other")
        stale = time.time() - core.LOCK_STALE_SECONDS - 5
        os.utime(self.lab.lock_path, (stale, stale))
        self.add("note", {"text": "x"})
        self.assertFalse(self.lab.lock_path.exists())


class TestSummary(LabCase):
    def test_statuses_and_corrections(self):
        h1 = self.hyp("First.")
        h2 = self.hyp("Second.")
        self.add("status", {"target": h1, "to": "testing", "rationale": "planned"})
        h1b = self.add("hypothesis", {"statement": "First, corrected.", "question": self.q, "origin": "a_priori"},
                       supersedes=h1)
        self.add("status", {"target": h2, "to": "abandoned", "rationale": "out of scope"})
        s = build(self.lab.read())
        by = {h["id"]: h for h in s["hypotheses"]}
        self.assertEqual(set(by), {h1, h2})
        self.assertEqual(by[h1]["statement"], "First, corrected.")
        self.assertEqual(by[h1]["corrected_by"], h1b)
        self.assertEqual(by[h1]["status"], "testing")
        self.assertEqual(by[h2]["status"], "abandoned")
        self.assertEqual(s["open_hypotheses"], [h1])

    def test_status_on_corrected_id_applies_to_root(self):
        h1 = self.hyp("First.")
        h1b = self.add("hypothesis", {"statement": "Fixed.", "question": self.q, "origin": "a_priori"},
                       supersedes=h1)
        self.add("status", {"target": h1b, "to": "testing", "rationale": "r"})
        s = build(self.lab.read())
        self.assertEqual(s["hypotheses"][0]["status"], "testing")


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"CLAIMSET_LAB": str(Path(self.tmp.name) / "lab")}
        self.old_env = {k: os.environ.get(k) for k in ("CLAIMSET_LAB", "CLAIMSET_ACTOR")}
        os.environ.update(self.env)
        os.environ.pop("CLAIMSET_ACTOR", None)

    def tearDown(self):
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue().strip(), err.getvalue().strip()

    def test_session(self):
        self.assertEqual(self.run_cli("lab", "init", "--title", "Study")[0], 0)
        self.assertEqual(self.run_cli("lab", "actor", "--kind", "human", "--name", "R")[1], "AC1")
        code, out, _ = self.run_cli("lab", "actor", "--kind", "agent", "--name", "A",
                                    "--model", "Anthropic|Claude Opus|5.5|claude-opus-5-5", "--by", "AC1")
        self.assertEqual(out, "AC2")
        os.environ["CLAIMSET_ACTOR"] = "AC2"
        self.assertEqual(self.run_cli("lab", "question", "Does X help?")[1], "Q1")
        self.assertEqual(self.run_cli("lab", "note", "pilot")[1], "NT1")
        self.assertEqual(self.run_cli("lab", "hypothesis", "X helps.", "--question", "Q1",
                                      "--from-exploration", "--prompted-by", "NT1")[1], "H1")
        code, _, err = self.run_cli("lab", "status", "H1", "supported", "--why", "pilot", "--evidence", "NT1")
        self.assertEqual(code, 1)
        self.assertIn("[L6]", err)
        self.assertEqual(self.run_cli("lab", "status", "H1", "testing", "--why", "plan next")[0], 0)
        code, out, _ = self.run_cli("lab", "summary", "--json")
        self.assertEqual(json.loads(out)["hypotheses"][0]["status"], "testing")
        code, out, _ = self.run_cli("lab", "verify")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("OK"))
        code, out, _ = self.run_cli("lab", "list", "--type", "hypothesis")
        self.assertIn("H1", out)
        code, out, _ = self.run_cli("lab", "show", "H1")
        self.assertEqual(json.loads(out)["event"]["id"], "H1")

    def test_write_requires_actor(self):
        self.run_cli("lab", "init", "--title", "Study")
        code, _, err = self.run_cli("lab", "question", "Q?")
        self.assertEqual(code, 1)
        self.assertIn("CLAIMSET_ACTOR", err)

    def test_artefact_is_hashed(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        data = Path(self.tmp.name) / "data.csv"
        data.write_text("a,b\n1,2\n")
        code, out, _ = self.run_cli("lab", "artefact", str(data), "--kind", "data",
                                    "--description", "Toy data.", "--by", "AC1", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["body"]["hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- plans and runs

import subprocess  # noqa: E402
import sys  # noqa: E402

from claimset.lab.run import run_command  # noqa: E402

SCRIPT = """
import json, os, sys
open(os.path.join(os.environ["CLAIMSET_RUN_DIR"], "out.txt"), "w").write("data")
json.dump([{"quantity": "q", "value": 0.5, "conditions": {"k": 1}}, {"quantity": "no value"}],
          open(os.environ["CLAIMSET_RESULTS"], "w"))
print("done")
sys.exit(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
"""


#: A deterministic decision rule on the result "q" that SCRIPT writes (0.5): its verdict is supported.
DECISION = {"framework": "deterministic", "quantities": {"q": {"quantity": "q"}},
            "supported": "q > 0", "not_supported": "q < 0"}


class PlanRunCase(LabCase):
    def setUp(self):
        super().setUp()
        self.h = self.hyp()
        self.script = Path(self.tmp.name) / "exp.py"
        self.script.write_text(SCRIPT)

    def plan(self, kind="confirmatory", **extra):
        body = {"kind": kind, "hypotheses": [self.h], "design": "d", "analysis": "a"}
        if kind == "confirmatory":
            body["decision"] = DECISION
        body.update(extra)
        return self.add("plan", {k: v for k, v in body.items() if v is not None})

    def execute(self, *args, **kw):
        return run_command(self.lab, self.agent, [sys.executable, str(self.script)] + list(args),
                           echo=False, cwd=self.tmp.name, **kw)


class TestPlansAndApprovals(PlanRunCase):
    def test_confirmatory_plan_needs_hypotheses(self):
        self.assertRefused("schema", "plan", {"kind": "confirmatory", "design": "d", "analysis": "a",
                                              "decision": DECISION})

    def test_confirmatory_plan_needs_decision_rule(self):
        self.assertRefused("L15", "plan", {"kind": "confirmatory", "hypotheses": [self.h],
                                           "design": "d", "analysis": "a", "criterion": "c"})

    def test_exploratory_plan_needs_no_criterion(self):
        self.plan(kind="exploratory", hypotheses=[], criterion=None)

    def test_approval_by_human_or_on_their_behalf(self):
        """Lab record v0.14, L22: a human approves, or an agent records the human's approval with their words."""
        pl = self.plan()
        self.assertRefused("L22", "approval", {"target": pl})                              # agent, own judgement
        self.assertRefused("L22", "approval", {"target": pl, "on_behalf_of": self.human})  # no words
        self.assertRefused("L22", "approval", {"target": pl, "on_behalf_of": self.agent,
                                               "words": "yes"})                            # not a human
        self.assertRefused("L22", "approval", {"target": pl, "on_behalf_of": self.human, "words": "x"},
                           by=self.human)                                                  # humans write as themselves
        self.assertRefused("schema", "approval", {"target": pl, "on_behalf_of": self.human, "words": "Yes.",
                                                  "given_at": "yesterday"})
        self.add("approval", {"target": pl, "comment": "ok"}, by=self.human)
        self.add("approval", {"target": pl, "on_behalf_of": self.human, "words": "Yes, go ahead with the plan.",
                              "given_at": "2026-10-29T11:30:00Z", "channel": "conversation"})

    def test_plan_requiring_approval_waits_for_it(self):
        """L23: no confirmatory run under a plan with requires_approval until an approval follows its version."""
        pl = self.plan(requires_approval=True)
        self.assertRefused("L23", "run", run_body(plan=pl, label="confirmatory"))
        self.add("run", run_body(plan=pl, label="exploratory"))                            # exploring is free
        self.add("approval", {"target": pl, "on_behalf_of": self.human, "words": "Approved."})
        pl2 = self.add("plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "changed", "analysis": "a",
                                "decision": DECISION, "requires_approval": True}, supersedes=pl)
        self.assertRefused("L23", "run", run_body(plan=pl, plan_version=pl2, label="confirmatory"))
        self.add("approval", {"target": pl2, "on_behalf_of": self.human, "words": "The change is fine."})
        self.add("run", run_body(plan=pl, plan_version=pl2, label="confirmatory"))
        self.assertRefused("schema", "plan", {"kind": "exploratory", "design": "d", "analysis": "a",
                                              "requires_approval": "yes"})

    def test_deviation_needs_reason(self):
        pl = self.plan()
        rn = self.add("run", run_body(plan=pl, label="confirmatory"))
        self.assertRefused("L7", "deviation", {"plan": pl, "run": rn, "text": "t"})


class TestRunRules(PlanRunCase):
    def test_l5_confirmatory_needs_plan(self):
        self.assertRefused("L5", "run", run_body(label="confirmatory"))

    def test_l5_exploratory_plan_cannot_confirm(self):
        pl = self.plan(kind="exploratory", criterion=None)
        self.assertRefused("L5", "run", run_body(plan=pl, label="confirmatory"))

    def test_run_needs_command(self):
        self.assertRefused("schema", "run", run_body(command=[]))

    def test_l4_result_source_must_be_output(self):
        rn = self.add("run", run_body())
        self.assertRefused("L4", "result", {"run": rn, "quantity": "q", "value": 1, "source": "other.json"})

    def test_result_value_is_number(self):
        rn = self.add("run", run_body())
        self.assertRefused("schema", "result", {"run": rn, "quantity": "q", "value": "1",
                                                "source": "results.json"})


class TestRunCommand(PlanRunCase):
    def test_exploratory_run_with_results(self):
        out = self.execute()
        run = out["run"]
        self.assertEqual(run["body"]["label"], "exploratory")
        self.assertEqual(run["body"]["exit_status"], "success")
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual([r["body"]["value"] for r in out["results"]], [0.5])
        self.assertEqual(len(out["problems"]), 1)
        rdir = self.dir / "runs" / run["id"]
        self.assertEqual(sorted(p.name for p in rdir.iterdir()),
                         ["out.txt", "results.json", "stderr.txt", "stdout.txt"])
        self.assertEqual((rdir / "stdout.txt").read_text().strip(), "done")
        self.assertTrue(verify(self.lab)["ok"])

    def test_confirmatory_under_confirmatory_plan(self):
        pl = self.plan()
        self.assertEqual(self.execute(plan=pl)["run"]["body"]["label"], "confirmatory")

    def test_exploratory_override(self):
        pl = self.plan()
        self.assertEqual(self.execute(plan=pl, exploratory=True)["run"]["body"]["label"], "exploratory")

    def test_failed_run_is_recorded(self):
        out = self.execute("3")
        self.assertEqual(out["run"]["body"]["exit_status"], "failed")
        self.assertEqual(out["exit_code"], 3)

    def test_unstartable_command_is_recorded(self):
        out = run_command(self.lab, self.agent, ["no-such-command-claimset"], echo=False, cwd=self.tmp.name)
        self.assertEqual(out["run"]["body"]["exit_status"], "crashed")
        rdir = self.dir / "runs" / out["run"]["id"]
        self.assertIn("could not start", (rdir / "stderr.txt").read_text())

    def test_parameters_seeds_and_data(self):
        data = Path(self.tmp.name) / "in.csv"
        data.write_text("1,2\n")
        body = self.execute(parameters={"n": 10}, seeds=[7], data=[str(data)])["run"]["body"]
        self.assertEqual(body["parameters"], {"n": 10})
        self.assertEqual(body["seeds"], [7])
        self.assertTrue(body["data"][0]["hash"].startswith("sha256:"))

    def test_l13_changed_added_and_missing_files(self):
        run = self.execute()["run"]
        rdir = self.dir / "runs" / run["id"]
        (rdir / "out.txt").write_text("changed")
        (rdir / "extra.txt").write_text("x")
        msgs = [e["message"] for e in verify(self.lab)["errors"]]
        self.assertTrue(any("changed after the run" in m for m in msgs))
        self.assertTrue(any("added to the run directory" in m for m in msgs))
        for f in rdir.iterdir():
            f.unlink()
        rdir.rmdir()
        self.assertIn("missing", verify(self.lab)["errors"][0]["message"])
        self.assertTrue(verify(self.lab, check_files=False)["ok"])

    def test_code_version_recorded_and_lab_excluded_from_dirty(self):
        repo = Path(self.tmp.name)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
        subprocess.run(git + ["add", "exp.py"], cwd=repo, check=True)
        subprocess.run(git + ["commit", "-qm", "init"], cwd=repo, check=True)
        body = self.execute()["run"]["body"]
        self.assertEqual(len(body["code_version"]), 40)
        self.assertFalse(body["dirty"])
        self.script.write_text(SCRIPT + "\n# edit\n")
        self.assertTrue(self.execute()["run"]["body"]["dirty"])

    def test_summary_plans_and_runs(self):
        pl = self.plan()
        self.add("approval", {"target": pl}, by=self.human)
        self.execute(plan=pl)
        self.execute("2")
        s = build(self.lab.read())
        self.assertEqual(s["plans"][0]["approved"], True)
        self.assertEqual(s["pending_plans"], [])
        self.assertEqual(s["runs"], {"total": 2, "confirmatory": 1, "exploratory": 1, "failed": 1})
        self.assertEqual(len(s["recent_results"]), 2)


class TestRunCli(TestCli):
    def test_run_returns_command_exit_code(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        script = Path(self.tmp.name) / "e.py"
        script.write_text("import sys; sys.exit(4)")
        code, _, err = self.run_cli("lab", "run", "--by", "AC1", "--", sys.executable, str(script))
        self.assertEqual(code, 4)
        self.assertIn("recorded RN1", err)
        self.assertIn("failed", err)
