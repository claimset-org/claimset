"""Decision rules, deciding runs and verdicts (lab record spec v0.5 §5.5, §5.7; rules L15-L17)."""

import json
import os
import sys
import unittest
from pathlib import Path

from claimset.lab import decision as dec
from claimset.lab.core import GENESIS_PREV, Lab, LabError, State, canonical, content_hash, verify
from claimset.lab.export import export
from claimset.lab.run import run_command
from claimset.validate import validate

from test_lab import LabCase, TestCli, ticking_clock

SCRIPT = """
import json, os, sys
d, lo, hi, p = (float(x) for x in sys.argv[1:5])
json.dump([{"quantity": "mean difference", "value": d, "interval": {"level": 0.95, "lower": lo, "upper": hi}},
           {"quantity": "p-value", "value": p}], open(os.environ["CLAIMSET_RESULTS"], "w"))
sys.exit(int(sys.argv[5]) if len(sys.argv) > 5 else 0)
"""

FREQ = {"framework": "frequentist",
        "parameters": {"alpha": 0.05, "test": "one-sided t-test", "sides": "greater", "correction": "none",
                       "sample": "200 series, fixed"},
        "quantities": {"p": {"quantity": "p-value"},
                       "d": {"quantity": "mean difference"},
                       "upper": {"quantity": "mean difference", "field": "interval.upper"}},
        "supported": "p < alpha and d > 0",
        "not_supported": "upper < 0.01"}


class RuleCase(LabCase):
    def setUp(self):
        super().setUp()
        self.h = self.hyp()
        self.script = Path(self.tmp.name) / "exp.py"
        self.script.write_text(SCRIPT)

    def plan(self, rule=FREQ, **extra):
        body = {"kind": "confirmatory", "hypotheses": [self.h], "design": "d", "analysis": "a", "decision": rule}
        body.update(extra)
        return self.add("plan", body)

    def run_(self, d=0.1, lo=0.05, hi=0.15, p=0.01, code=0, plan=None, exploratory=False):
        out = run_command(self.lab, self.agent, [sys.executable, str(self.script), str(d), str(lo), str(hi), str(p),
                                                 str(code)], plan=plan, exploratory=exploratory, echo=False,
                          cwd=self.tmp.name)
        return out["run"]["id"], [r["id"] for r in out["results"]]

    def verdict_status(self, to, evidence, plan, **extra):
        body = {"target": self.h, "to": to, "rationale": "r", "evidence": evidence, "plan": plan}
        body.update(extra)
        return self.add("status", body)

    def refused(self, expected, fn, *a, **kw):
        with self.assertRaises(LabError) as cm:
            fn(*a, **kw)
        self.assertEqual(cm.exception.rule, expected, cm.exception.message)
        return cm.exception.message


class TestRuleChecks(unittest.TestCase):
    def test_rule_size_and_constants_limited(self):
        for bad in ["p * " + "9" * 400 + " < 1", "+".join(["p"] * 600) + " > 0"]:
            with self.assertRaises(dec.RuleError):
                dec.parse(bad, {"p"})

    def test_complete_rules_pass(self):
        self.assertEqual(dec.problems(FREQ), [])
        self.assertEqual(dec.problems({"framework": "formal", "supported": "A proof of the theorem.",
                                       "not_supported": "A counterexample."}), [])

    def test_required_parameters_per_framework(self):
        for fw, needed in dec.REQUIRED.items():
            text = " ".join(dec.problems({"framework": fw, "quantities": {"x": {"quantity": "q"}},
                                          "supported": "x > 0", "not_supported": "x < 0"}))
            for key in needed:
                self.assertIn(f"'{key}'", text, (fw, key))

    def test_parameter_values(self):
        bad = dict(FREQ, parameters=dict(FREQ["parameters"], alpha=1.5, sides="up"))
        text = " ".join(dec.problems(bad))
        self.assertIn("alpha", text)
        self.assertIn("sides", text)
        eq = {"framework": "equivalence", "parameters": {"alpha": 0.05, "test": "TOST", "lower_bound": 0.1,
                                                         "upper_bound": -0.1, "sample": "n"},
              "quantities": {"x": {"quantity": "q"}}, "supported": "x > 0", "not_supported": "x < 0"}
        self.assertIn("lower_bound must be below upper_bound", " ".join(dec.problems(eq)))

    def test_rules_allow_only_arithmetic_and_logic(self):
        names = {"a", "b"}
        for ok in ["a < 0.05", "0 < a <= b", "abs(a - b) < 0.1 and not b > 2", "-a * 2 / b >= 1 or a == b"]:
            dec.parse(ok, names)
        for bad in ["a.real > 0", "__import__('os')", "max(a, b) > 1", "'x' == a", "lambda: 1", "a if b else 1",
                    "[a][0] > 1", "c > 1", "True", "", "a <"]:
            with self.assertRaises(dec.RuleError, msg=bad):
                dec.parse(bad, names)

    def test_quantities_and_text_rules_checked(self):
        no_q = {k: v for k, v in FREQ.items() if k != "quantities"}
        self.assertEqual(dec.problems(no_q), [])            # text rules, judged verdict
        self.assertFalse(dec.computed(no_q))
        clash = dict(FREQ, quantities=dict(FREQ["quantities"], alpha={"quantity": "x"}))
        self.assertIn("both a quantity and a parameter", " ".join(dec.problems(clash)))
        bad_field = dict(FREQ, quantities={"p": {"quantity": "p-value", "field": "interval.middle"}})
        self.assertTrue(any("field" in x for x in dec.problems(bad_field)))
        self.assertTrue(dec.problems({"framework": "qualitative", "supported": "", "not_supported": "x"}))

    def test_verdicts(self):
        res = [{"quantity": "p-value", "value": 0.01}, {"quantity": "mean difference", "value": 0.2,
                                                         "interval": {"lower": 0.1, "upper": 0.3}}]
        self.assertEqual(dec.verdict(FREQ, res)["verdict"], "supported")
        res[0]["value"] = 0.2
        self.assertEqual(dec.verdict(FREQ, res)["verdict"], "inconclusive")
        res[1].update(value=0.001, interval={"lower": -0.002, "upper": 0.004})
        self.assertEqual(dec.verdict(FREQ, res)["verdict"], "not_supported")

    def test_overlap_and_binding_problems_are_inconclusive(self):
        overlap = dict(FREQ, not_supported="d > 0")
        res = [{"quantity": "p-value", "value": 0.01}, {"quantity": "mean difference", "value": 0.2,
                                                         "interval": {"lower": 0.1, "upper": 0.3}}]
        out = dec.verdict(overlap, res)
        self.assertEqual(out["verdict"], "inconclusive")
        self.assertIn("overlap", out["problems"][0])
        out = dec.verdict(FREQ, res + [{"quantity": "p-value", "value": 0.5}])
        self.assertEqual(out["verdict"], "inconclusive")
        self.assertIn("matches 2 results", out["problems"][0])

    def test_conditions_select_results(self):
        rule = {"framework": "deterministic", "quantities": {"m": {"quantity": "q", "conditions": {"k": 2}}},
                "supported": "m > 1", "not_supported": "m <= 1"}
        res = [{"quantity": "q", "value": 0, "conditions": {"k": 1}}, {"quantity": "q", "value": 5, "conditions": {"k": 2}}]
        self.assertEqual(dec.verdict(rule, res)["verdict"], "supported")


class TestPlans(RuleCase):
    def test_l15_incomplete_rule_refused(self):
        msg = self.refused("L15", self.plan, rule=dict(FREQ, parameters={"alpha": 0.05}))
        self.assertIn("'sample'", msg)
        self.refused("L15", self.plan, rule=dict(FREQ, supported="p < alpha and q > 0"))
        self.refused("L15", self.plan, runs=0)
        self.refused("L15", self.add, "plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "d",
                                               "analysis": "a", "criterion": "c"})

    def test_run_records_plan_version(self):
        pl = self.plan()
        fixed = self.add("plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "d2", "analysis": "a",
                                  "decision": FREQ}, supersedes=pl)
        rn, _ = self.run_(plan=pl)
        self.assertEqual(self.lab.state().get(rn)["body"]["plan_version"], fixed)


class TestDecidingRuns(RuleCase):
    def test_l16_no_confirmatory_run_after_deciding_runs(self):
        pl = self.plan()
        self.run_(plan=pl)                          # deciding run
        msg = self.refused("L16", self.run_, plan=pl)
        self.assertIn("new plan", msg)
        self.run_(plan=pl, exploratory=True)        # still allowed as exploratory
        self.assertTrue(verify(self.lab)["ok"])

    def test_failed_run_counts(self):
        pl = self.plan()
        rn, rs = self.run_(d=-0.1, lo=-0.2, hi=-0.05, p=0.9, code=1, plan=pl)   # writes results, then fails
        self.refused("L16", self.run_, plan=pl)
        self.verdict_status("not_supported", rs, pl)

    def test_several_deciding_runs(self):
        self.refused("L15", self.plan, rule=dict(FREQ, quantities={"p": {"quantity": "p-value"}},
                                                 supported="p < alpha", not_supported="p > 0.5"), runs=2)
        rule = dict(FREQ, quantities={"p1": {"quantity": "p-value", "run": 1}, "p2": {"quantity": "p-value", "run": 2}},
                    supported="p1 < alpha and p2 < alpha", not_supported="p1 > 0.5 and p2 > 0.5")
        pl = self.plan(rule=rule, runs=2)
        rn1, rs1 = self.run_(plan=pl)
        self.refused("L17", self.verdict_status, "supported", rs1, pl)          # not decided yet
        rn2, rs2 = self.run_(p=0.02, plan=pl)
        self.refused("L16", self.run_, plan=pl)
        self.verdict_status("supported", rs1 + rs2, pl)

    def test_correction_after_deciding_run_does_not_change_verdict(self):
        pl = self.plan()
        rn, rs = self.run_(d=0.001, lo=-0.002, hi=0.004, p=0.4, plan=pl)
        self.add("plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "d", "analysis": "a",
                          "decision": dict(FREQ, supported="d > 0")}, supersedes=pl)
        self.refused("L17", self.verdict_status, "supported", rs, pl)
        self.verdict_status("not_supported", rs, pl)


class TestReporting(RuleCase):
    """Every planned test that ran is reported with its verdict (§5.7.4); exploration is not restricted."""

    def claims(self):
        return export(self.lab)["draft"]["claims"]

    def test_statuses_before_the_test_do_not_replace_its_verdict(self):
        pl = self.plan()
        rn, rs = self.run_()                                         # exploratory, favourable
        self.add("status", {"target": self.h, "to": "supported", "rationale": "r", "evidence": rs})
        crn, crs = self.run_(d=-0.1, lo=-0.2, hi=-0.05, p=0.9, plan=pl)   # the planned test: refuted
        with self.assertRaises(LabError) as cm:
            export(self.lab)
        self.assertIn("verdict is not recorded", cm.exception.message)
        self.verdict_status("not_supported", crs, pl)
        [claim] = self.claims()
        self.assertEqual((claim["hypothesis"]["outcome"], claim["planning"]), ("not_supported", "confirmatory"))

    def test_abandoning_does_not_hide_a_test_that_ran(self):
        pl = self.plan()
        self.add("status", {"target": self.h, "to": "abandoned", "rationale": "r"})
        rn, rs = self.run_(d=-0.1, lo=-0.2, hi=-0.05, p=0.9, plan=pl)
        self.refused("export", export, self.lab)
        self.verdict_status("not_supported", rs, pl)
        self.add("status", {"target": self.h, "to": "abandoned", "rationale": "dropped after the test"})
        out = export(self.lab)
        self.assertEqual([c["hypothesis"]["outcome"] for c in out["draft"]["claims"]], ["not_supported"])
        self.assertEqual(out["draft"]["paper"]["exploration"]["hypotheses"]["abandoned"], 1)
        self.assertEqual(out["draft"]["paper"]["exploration"]["tests"]["not_supported"], 1)

    def test_retest_reports_both_verdicts(self):
        pl1 = self.plan()
        rn1, rs1 = self.run_(d=0.2, lo=-0.1, hi=0.5, p=0.2, plan=pl1)
        self.verdict_status("inconclusive", rs1, pl1)
        pl2 = self.plan(design="larger sample")
        rn2, rs2 = self.run_(plan=pl2)
        self.verdict_status("supported", rs2, pl2)
        claims = self.claims()
        self.assertEqual([(c["hypothesis"]["outcome"], c["test"]["lab_plan"]) for c in claims],
                         [("inconclusive", pl1), ("supported", pl2)])
        out = export(self.lab)
        self.assertEqual(out["draft"]["paper"]["exploration"]["tests"],
                         {"total": 2, "supported": 1, "not_supported": 0, "inconclusive": 1})
        self.assertFalse([e for e in validate(out["draft"]).errors if e["rule"] in ("45", "46", "47")])

    def test_rewording_after_the_test_does_not_move_the_verdict(self):
        pl = self.plan()
        rn, rs = self.run_(plan=pl)
        self.verdict_status("supported", rs, pl)
        self.add("hypothesis", {"statement": "A broader claim.", "question": self.q, "origin": "a_priori"},
                 supersedes=self.h)
        [claim] = self.claims()
        self.assertEqual(claim["statement"], "X improves Y.")
        self.assertEqual(claim["hypothesis"]["lab_event"], self.h)

    def test_citing_deciding_run_needs_plan(self):
        pl = self.plan()
        rn, rs = self.run_(plan=pl)
        msg = self.refused("L17", self.add, "status", {"target": self.h, "to": "supported", "rationale": "r",
                                                       "evidence": rs})
        self.assertIn("--plan", msg)

    def test_open_hypotheses_are_counted(self):
        self.hyp("Never tested.")
        out = export(self.lab)
        self.assertEqual(out["draft"]["paper"]["exploration"]["hypotheses"]["open"], 2)

    def test_agent_runs_only_its_own_plans(self):
        pl = self.plan()
        self.lab.start_agent(self.agent)
        self.refused("L16", self.run_, plan=pl)
        own = self.add("plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "d", "analysis": "a",
                                "decision": FREQ})
        self.run_(plan=own)

    def test_text_rules_give_judged_verdicts(self):
        rule = {k: v for k, v in FREQ.items() if k != "quantities"}
        rule.update(supported="p-value below alpha with a positive mean difference",
                    not_supported="upper 95% bound of the mean difference below 0.01")
        pl = self.plan(rule=rule)
        rn, rs = self.run_(plan=pl)
        self.refused("L17", self.verdict_status, "supported", rs, pl)
        self.verdict_status("supported", rs, pl, rule="supported")
        [claim] = self.claims()
        self.assertEqual(claim["test"]["verdict"], "judged")


class TestVerdicts(RuleCase):
    def test_verdict_must_follow_rule(self):
        pl = self.plan()
        rn, rs = self.run_(d=0.2, lo=-0.1, hi=0.5, p=0.2, plan=pl)
        msg = self.refused("L17", self.verdict_status, "not_supported", rs, pl)
        self.assertIn("gives inconclusive", msg)
        self.assertIn("p = 0.2", msg)
        self.refused("L17", self.verdict_status, "supported", rs, pl)
        self.verdict_status("inconclusive", rs, pl)
        self.assertTrue(verify(self.lab)["ok"])

    def test_verdict_names_plan_and_cites_deciding_run(self):
        pl = self.plan()
        rn, rs = self.run_(plan=pl)
        self.refused("L17", self.add, "status", {"target": self.h, "to": "supported", "rationale": "r",
                                                 "evidence": rs})
        other_rn, other_rs = self.run_()
        self.refused("L17", self.verdict_status, "supported", other_rs, pl)
        self.verdict_status("supported", rs, pl)

    def test_undecided_plan_cannot_be_named(self):
        pl = self.plan()
        rn, rs = self.run_()
        msg = self.refused("L17", self.verdict_status, "supported", rs, pl)
        self.assertIn("not had its deciding runs", msg)

    def test_binding_failure_allows_only_inconclusive(self):
        rule = dict(FREQ, quantities=dict(FREQ["quantities"], p={"quantity": "missing quantity"}))
        pl = self.plan(rule=rule)
        rn, rs = self.run_(plan=pl)
        msg = self.refused("L17", self.verdict_status, "supported", rs, pl)
        self.assertIn("matches 0 results", msg)
        self.verdict_status("inconclusive", rs, pl)

    def test_text_rules_need_stated_rule(self):
        rule = {"framework": "qualitative", "supported": "Every interviewee describes X.",
                "not_supported": "No interviewee describes X."}
        pl = self.plan(rule=rule)
        rn, rs = self.run_(plan=pl)
        self.refused("L17", self.verdict_status, "supported", [rn], pl)
        self.refused("L17", self.verdict_status, "supported", [rn], pl, rule="neither")
        self.verdict_status("supported", [rn], pl, rule="supported")


class TestWithheldAndMalformed(RuleCase):
    def withhold(self, eid):
        events = [json.loads(l) for l in self.lines()]
        for e in events:
            if e["id"] == eid:
                e["body"] = {"withheld": True, "reason": "commercial"}
        self.write_lines([canonical(e).decode("utf-8") for e in events])

    def test_withheld_deciding_result_or_run_is_not_a_violation(self):
        pl = self.plan()
        rn, rs = self.run_(plan=pl)
        self.verdict_status("supported", rs, pl)
        self.withhold(rs[0])
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])
        self.withhold(rn)
        self.assertTrue(verify(self.lab, check_files=False)["ok"])

    def test_non_finite_results_are_reported_not_recorded(self):
        script = Path(self.tmp.name) / "nan.py"
        script.write_text("import json, os\nopen(os.environ['CLAIMSET_RESULTS'], 'w').write("
                          "'[{\"quantity\": \"q\", \"value\": NaN}]')\n")
        out = run_command(self.lab, self.agent, [sys.executable, str(script)], echo=False, cwd=self.tmp.name)
        self.assertEqual(out["results"], [])
        self.assertIn("finite", out["problems"][0])


class TestFlags(RuleCase):
    """Warnings, not refusals: code changed after the plan, held-out data used elsewhere (§5.7.5)."""

    def git(self, *args):
        import subprocess
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.tmp.name, check=True,
                       capture_output=True)

    def flags_of(self, pl):
        st = self.lab.state()
        from claimset.lab.core import plan_flags
        pv, runs = st.deciding_runs(pl)
        return plan_flags(st, pv, runs)

    def test_clean_test_has_no_flags(self):
        self.git("init", "-q")
        self.git("add", "exp.py")
        self.git("commit", "-qm", "code")
        from claimset.lab.run import code_version
        commit, dirty = code_version(self.tmp.name, self.lab.root)
        pl = self.plan(code_version=commit, dirty=dirty)
        rn, rs = self.run_(plan=pl)
        self.assertEqual(self.flags_of(pl), [])

    def test_code_changed_after_plan(self):
        self.git("init", "-q")
        self.git("add", "exp.py")
        self.git("commit", "-qm", "code")
        from claimset.lab.run import code_version
        commit, dirty = code_version(self.tmp.name, self.lab.root)
        pl = self.plan(code_version=commit, dirty=dirty)
        self.script.write_text(SCRIPT + "\n# tuned after the plan\n")
        rn, rs = self.run_(plan=pl)
        self.assertEqual(self.flags_of(pl), ["code_changed_after_plan"])
        self.verdict_status("supported", rs, pl)
        out = export(self.lab)
        self.assertEqual(out["draft"]["claims"][0]["test"]["flags"], ["code_changed_after_plan"])
        self.assertFalse([e for e in validate(out["draft"]).errors if e["rule"] == "47"])
        self.assertTrue(verify(self.lab)["ok"])
        self.assertTrue(any("code changed" in n for n in verify(self.lab)["notes"]))

    def test_holdout_used_in_exploration(self):
        data = Path(self.tmp.name) / "holdout.csv"
        data.write_text("1,2\n")
        from claimset.lab.core import file_hash
        pl = self.plan(holdout=[{"path": "holdout.csv", "hash": file_hash(data)}])
        run_command(self.lab, self.agent, [sys.executable, str(self.script), "0.1", "0", "0.2", "0.5"],
                    data=[str(data)], echo=False, cwd=self.tmp.name)
        rn, rs = self.run_(plan=pl)
        self.assertEqual(self.flags_of(pl), ["holdout_used_elsewhere"])


class TestOlderRecords(LabCase):
    def test_v04_record_keeps_its_meaning(self):
        old = Lab(Path(self.tmp.name) / "old", clock=ticking_clock())
        old.root.mkdir()
        old.append("init", {"lab_spec_version": "0.4", "working_title": "t"}, by=None)
        hum = old.append("actor", {"kind": "human", "name": "H"}, by="AC1")["id"]
        q = old.append("question", {"text": "q"}, by=hum)["id"]
        h = old.append("hypothesis", {"statement": "s", "question": q, "origin": "a_priori"}, by=hum)["id"]
        pl = old.append("plan", {"kind": "confirmatory", "hypotheses": [h], "design": "d", "analysis": "a",
                                 "criterion": "c"}, by=hum)["id"]
        body = {"plan": pl, "label": "confirmatory", "command": ["x"], "exit_status": "success", "outputs": []}
        old.append("run", body, by=hum)
        old.append("run", body, by=hum)              # no L16 for v0.4 records
        old.append("status", {"target": h, "to": "abandoned", "rationale": "r"}, by=hum)   # no L17
        self.assertTrue(verify(old)["ok"])
        out = export(old)
        self.assertEqual(out["draft"]["claims"], [])


class TestExportCarriesRule(RuleCase):
    def test_claim_has_test(self):
        pl = self.plan()
        rn, rs = self.run_(plan=pl)
        self.verdict_status("supported", rs, pl)
        out = export(self.lab)
        c = out["draft"]["claims"][0]
        self.assertEqual(c["test"]["framework"], "frequentist")
        self.assertEqual(c["test"]["parameters"]["alpha"], 0.05)
        self.assertEqual(c["test"]["lab_plan"], pl)
        self.assertEqual(c["test"]["runs"], 1)
        self.assertFalse([e for e in validate(out["draft"]).errors if e["rule"] == "47"])


class TestCliRules(TestCli):
    def test_plan_verdict_and_status(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        self.run_cli("lab", "question", "Q?")
        self.run_cli("lab", "hypothesis", "H.", "--question", "Q1")
        rule_file = Path(self.tmp.name) / "rule.json"
        rule_file.write_text(json.dumps({"framework": "frequentist", "parameters": {"test": "t", "sides": "greater",
                                                                                    "correction": "none"}}))
        code, _, err = self.run_cli(
            "lab", "plan", "--kind", "confirmatory", "--hypotheses", "H1", "--design", "d", "--analysis", "a",
            "--decision", str(rule_file), "--parameter", "alpha=0.05", "--parameter", "sample=200 series",
            "--quantity", "p=p-value", "--quantity", 'd={"quantity": "mean difference", "field": "value"}',
            "--quantity", 'upper={"quantity": "mean difference", "field": "interval.upper"}',
            "--supported", "p < alpha and d > 0", "--not-supported", "upper < 0.01")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("lab", "verdict", "PL1")
        self.assertIn("Not decided yet", out)
        script = Path(self.tmp.name) / "e.py"
        script.write_text(SCRIPT)
        self.run_cli("lab", "run", "--plan", "PL1", "--", sys.executable, str(script), "0.2", "0.1", "0.3", "0.01")
        code, out, _ = self.run_cli("lab", "verdict", "PL1")
        self.assertIn("Verdict: supported", out)
        self.assertIn("p = 0.01", out)
        code, _, err = self.run_cli("lab", "status", "H1", "inconclusive", "--why", "w", "--evidence", "RS1", "RS2",
                                    "--plan", "PL1")
        self.assertEqual(code, 1)
        self.assertIn("[L17]", err)
        code, _, err = self.run_cli("lab", "status", "H1", "supported", "--why", "w", "--evidence", "RS1", "RS2",
                                    "--plan", "PL1")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
