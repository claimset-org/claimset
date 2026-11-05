"""Rules for each hypothesis of a plan (lab record spec v0.16 §5.7.1; rules L15, L17; export §9)."""

import json
import unittest
from pathlib import Path

from claimset.lab import decision as dec
from claimset.lab.core import verify
from claimset.lab.export import export

from test_lab import TestCli
from test_lab_decision import FREQ, SCRIPT, RuleCase

SHARED = {k: v for k, v in FREQ.items() if k not in ("supported", "not_supported")}
TWO = dict(SHARED, rules={
    "H1": {"supported": "p < alpha and d > 0", "not_supported": "upper < 0.01"},
    "H2": {"framework": "deterministic", "parameters": {"large": 0.5},
           "supported": "d > large", "not_supported": "d <= large"}})


class TestRuleChecks(unittest.TestCase):
    def test_effective_rule_of_each_hypothesis(self):
        h1, h2 = dec.effective(TWO, "H1"), dec.effective(TWO, "H2")
        self.assertEqual((h1["framework"], h1["supported"]), ("frequentist", "p < alpha and d > 0"))
        self.assertEqual((h2["framework"], h2["supported"], h2["parameters"]["large"], h2["parameters"]["alpha"]),
                         ("deterministic", "d > large", 0.5, 0.05))
        self.assertNotIn("rules", h1)
        self.assertIs(dec.effective(FREQ, "H1"), FREQ)          # one pair decides every hypothesis, as before
        self.assertEqual(dec.names_used(h2), {"d", "large"})

    def test_complete_rules_pass(self):
        self.assertEqual(dec.problems(TWO, hypotheses=["H1", "H2"]), [])

    def test_broken_rules_fail(self):
        def text(rule, hyps=("H1", "H2")):
            return " | ".join(dec.problems(rule, hypotheses=list(hyps)))
        self.assertIn("no entry for H2", text(dict(SHARED, rules={"H1": TWO["rules"]["H1"]})))
        self.assertIn("H3, which the plan does not test", text(dict(TWO, rules=dict(TWO["rules"], H3=TWO["rules"]["H1"]))))
        self.assertIn("no shared 'supported' rule", text(dict(TWO, supported="p < alpha")))
        self.assertIn("an entry has only", text(dict(TWO, rules=dict(TWO["rules"], H2=dict(TWO["rules"]["H2"],
                                                                                          quantities={})))))
        self.assertIn("rules for H2: the 'not_supported' rule", text(dict(TWO, rules=dict(
            TWO["rules"], H2={"supported": "d > 0"}))))
        self.assertIn("one entry per hypothesis", text(dict(SHARED, rules={})))
        self.assertIn("rules for H2: the bayesian framework needs the parameter 'prior'",
                      text(dict(TWO, rules=dict(TWO["rules"], H2=dict(TWO["rules"]["H2"], framework="bayesian")))))


class TestPlanWithTwoRules(RuleCase):
    """Two hypotheses decided by different rules from one computation: one plan, one deciding run, a verdict each."""

    def setUp(self):
        super().setUp()
        self.h2 = self.hyp("X improves Y by more than half a unit.")
        self.rule = json.loads(json.dumps(TWO).replace('"H1"', f'"{self.h}"').replace('"H2"', f'"{self.h2}"'))

    def test_one_run_decides_each_hypothesis_by_its_own_rule(self):
        pl = self.plan(self.rule, hypotheses=[self.h, self.h2])
        rn, res = self.run_(d=0.1, lo=0.05, hi=0.15, p=0.01, plan=pl)
        self.refused("L17", self.verdict_status, "supported", [rn], pl, target=self.h2)
        self.verdict_status("supported", [rn], pl)
        self.verdict_status("not_supported", [rn], pl, target=self.h2)
        rep = verify(self.lab)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertFalse(any("repeats" in n or "holdout" in n for n in rep["notes"]), rep["notes"])
        claims = {c["hypothesis"]["lab_event"]: c for c in export(self.lab)["draft"]["claims"]}
        t1, t2 = claims[self.h]["test"], claims[self.h2]["test"]
        self.assertEqual((t1["framework"], t1["supported"], set(t1["quantities"])),
                         ("frequentist", "p < alpha and d > 0", {"p", "d", "upper"}))
        self.assertEqual((t2["framework"], t2["supported"], set(t2["quantities"]), t2["parameters"]["large"]),
                         ("deterministic", "d > large", {"d"}, 0.5))
        self.assertEqual((claims[self.h]["hypothesis"]["outcome"], claims[self.h2]["hypothesis"]["outcome"]),
                         ("supported", "not_supported"))
        self.assertNotIn("rules", t1)

    def test_a_plan_without_a_rule_for_each_hypothesis_is_refused(self):
        partial = dict(self.rule, rules={self.h: self.rule["rules"][self.h]})
        msg = self.refused("L15", self.plan, partial, hypotheses=[self.h, self.h2])
        self.assertIn(f"no entry for {self.h2}", msg)

    def test_a_corrected_hypothesis_is_decided_by_the_rule_of_the_version_the_plan_names(self):
        pl = self.plan(self.rule, hypotheses=[self.h, self.h2])
        h2b = self.add("hypothesis", {"statement": "X improves Y by more than half a unit, on all series.",
                                      "question": self.q, "origin": "a_priori"}, supersedes=self.h2)
        rn, _ = self.run_(d=0.9, lo=0.8, hi=1.0, p=0.001, plan=pl)
        self.verdict_status("supported", [rn], pl, target=h2b)
        self.verdict_status("supported", [rn], pl)


class TestLabRecordVersions(RuleCase):
    def test_the_lab_record_artefact_names_its_spec_and_tool_versions(self):
        from claimset import __version__
        self.add("status", {"target": self.h, "to": "abandoned", "rationale": "r"})
        art = next(a for a in export(self.lab)["draft"]["artefacts"] if a["kind"] == "lab_record")
        init = self.lab.read()[0]["body"]
        self.assertIn(f"specification v{init['lab_spec_version']}, begun {init['started_at'][:10]}", art["description"])
        self.assertIn(f"exported with claimset {__version__}", art["description"])
        self.assertNotIn("v0.5)", art["description"])


class TestCliRules(TestCli):
    def test_prefixed_rules_give_each_hypothesis_its_own(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        self.run_cli("lab", "question", "Q?")
        self.run_cli("lab", "hypothesis", "X improves Y.", "--question", "Q1")
        self.run_cli("lab", "hypothesis", "X improves Y a lot.", "--question", "Q1")
        code, out, err = self.run_cli(
            "lab", "plan", "--kind", "confirmatory", "--hypotheses", "H1", "H2", "--design", "d", "--analysis", "a",
            "--framework", "deterministic", "--parameter", "threshold=0.1", "--quantity", "s=share",
            "--quantity", "n=total", "--supported", "H1: s < threshold", "--not-supported", "H1: s >= threshold",
            "--supported", "H2: n > 3", "--not-supported", "H2: n <= 3")
        self.assertEqual(code, 0, err)
        plan = [json.loads(l) for l in (Path(self.env["CLAIMSET_LAB"]) / "lab.jsonl").read_text().splitlines()][-1]
        self.assertEqual(plan["body"]["decision"]["rules"],
                         {"H1": {"supported": "s < threshold", "not_supported": "s >= threshold"},
                          "H2": {"supported": "n > 3", "not_supported": "n <= 3"}})
        code, out, _ = self.run_cli("lab", "verdict", "PL1")
        self.assertIn("PL1 H1 (deterministic): supported if s < threshold", out)
        self.assertIn("PL1 H2 (deterministic): supported if n > 3", out)


import os  # noqa: E402  (used by TestCliRules)

if __name__ == "__main__":
    unittest.main()
