"""Tests for the lab record core (lab record spec v0.3)."""

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
        run = self.add("run", {"label": "exploratory", "exit_status": "success"})
        res = self.add("result", {"run": run, "quantity": "q", "value": 1.0})
        h = self.hyp(origin="from_exploration", prompted_by=[run])
        self.assertRefused("L6", "status", {"target": h, "to": "supported", "rationale": "r", "evidence": [res]})

    def test_l6_new_evidence_can_confirm(self):
        n = self.add("note", {"text": "pilot"})
        h = self.hyp(origin="from_exploration", prompted_by=[n])
        run = self.add("run", {"label": "confirmatory", "exit_status": "success"})
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
