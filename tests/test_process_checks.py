"""Process checks (claimset spec v0.20: warnings 17-21, rule 58, flag reproduces_prompting_result; lab record
spec v0.8 §5.7.5-5.7.6). The fixture is a real study (tests/fixtures/adam-admissible-bounds/SOURCE.md); every
check is shown to fire on it where expected, and not to fire on the valid examples."""

import copy
import json
import sys
import unittest
from pathlib import Path

from claimset import cli
from claimset.lab.core import (Lab, State, duplicate_runs, plan_flags, plan_warnings, reproduced_results,
                               uncounted_exclusions, verify)
from claimset.lab.export import export
from claimset.lab.run import run_command
from claimset.validate import SCHEMA_VERSION, validate

from test_lab import TestCli
from test_lab_decision import FREQ, RuleCase

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "adam-admissible-bounds"
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))
NEW = ("W17", "W18", "W19", "W20", "W21")


def fixture_claimset():
    d = json.loads((FIXTURE / "claimset.json").read_text(encoding="utf-8"))
    d["schema_version"] = SCHEMA_VERSION      # written under v0.18; the checks are what is tested here
    for c in d["claims"]:                     # the novelty labels as renamed in v0.22
        if any(s.get("kind") == "assumption" for s in c["support"]):
            c.pop("novelty", None)
        elif c.get("novelty") == "new":
            c["novelty"] = "no_prior_found"
    return d


def fixture_state():
    st = State()
    for line in (FIXTURE / "lab" / "lab.jsonl").read_text(encoding="utf-8").splitlines():
        st.apply(json.loads(line))
    return st


def warned(rep, rule):
    """{object ID: [warning messages]} for one warning rule."""
    out = {}
    for w in rep.warnings:
        if w["rule"] == rule:
            out.setdefault(w["where"].split(":", 1)[1].split(".")[0], []).append(w["message"])
    return out


def example(name="submission-native.json"):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class TestFixtureClaimset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep = validate(fixture_claimset())

    def test_fixture_is_valid(self):
        self.assertEqual(self.rep.errors, [])

    def test_w17_number_only_in_a_description(self):
        w = warned(self.rep, "W17")
        self.assertIn("C4", w)
        self.assertIn("536", w["C4"][0])
        self.assertNotIn("C1", w)
        self.assertNotIn("C3", w)

    def test_w18_duplicate_evidence(self):
        self.assertEqual(list(warned(self.rep, "W18")), ["E2"])
        self.assertIn("E1", warned(self.rep, "W18")["E2"][0])

    def test_w19_not_raised_when_consensus_is_not_the_only_support(self):
        # C17 and C20 are new, with a formal proof and consensus: the narrow check leaves them to the author
        self.assertEqual(warned(self.rep, "W19"), {})

    def test_w20_unmitigated_reversal_risk(self):
        w = warned(self.rep, "W20")
        for cid in ("C1", "C3", "C16"):
            self.assertIn("L2", w[cid][0])

    def test_w21_branch_link(self):
        self.assertEqual(list(warned(self.rep, "W21")), ["A9"])


class TestExamplesStayClean(unittest.TestCase):
    def test_no_new_warning_on_the_examples(self):
        for path in EXAMPLES:
            rep = validate(json.loads(path.read_text(encoding="utf-8")), published=path.name.startswith("published"))
            self.assertEqual(rep.errors, [], path.name)
            self.assertEqual([w for w in rep.warnings if w["rule"] in NEW], [], path.name)


class TestValidatorChecks(unittest.TestCase):
    """A valid case and a broken case for each check."""

    def claim(self, d, cid):
        return next(c for c in d["claims"] if c["id"] == cid)

    def test_w17_number_recorded_as_a_result_is_sourced(self):
        d = fixture_claimset()
        rep = validate(d)
        self.assertIn("C4", warned(rep, "W17"))
        c4 = self.claim(d, "C4")
        ev = next(e for e in d["evidence"] if e["id"] == c4["support"][0]["evidence"])
        ev.setdefault("results", []).append({"id": "R99", "quantity": "models in the grid", "value": 536})
        self.assertNotIn("536", " ".join(warned(validate(d), "W17").get("C4", [])))

    def test_w17_ignores_model_notation_and_markers(self):
        d = example()
        c = next(c for c in d["claims"] if any(s.get("kind") in ("empirical_quant", "simulation")
                                               for s in c["support"]))
        c["statement"] = c["statement"].rstrip(".") + " for ETS(A,N,A) and AR(1) models (Section 4.2)."
        self.assertEqual(warned(validate(d), "W17"), {})
        c["statement"] = c["statement"].rstrip(".") + ", on 7311 series."
        self.assertIn("7311", warned(validate(d), "W17")[c["id"]][0])

    def test_w18_needs_identical_results(self):
        d = fixture_claimset()
        e2 = next(e for e in d["evidence"] if e["id"] == "E2")
        e2["results"][0]["value"] = e2["results"][0]["value"] + 1
        self.assertEqual(warned(validate(d), "W18"), {})

    def test_w19_new_claim_resting_on_consensus_only(self):
        d = example("submission-native.json")
        c = next(c for c in d["claims"] if c["id"] == "C2")
        c["support"] = [{"kind": "consensus"}]
        self.assertIn(c["id"], warned(validate(d), "W19"))
        c["novelty"] = "extension"
        self.assertEqual(warned(validate(d), "W19"), {})

    def test_w20_silenced_by_a_rationale_naming_the_limitation_or_a_low_strength(self):
        d = fixture_claimset()
        c1 = self.claim(d, "C1")
        a = next(a for a in c1["assessments"] if a["by"] == "author")
        a["rationale"] += " L2 was weighed: the grid is exhaustive."
        self.assertNotIn("C1", warned(validate(d), "W20"))
        d = fixture_claimset()
        next(a for a in self.claim(d, "C3")["assessments"] if a["by"] == "author")["strength"] = "low"
        self.assertNotIn("C3", warned(validate(d), "W20"))

    def test_w20_not_raised_for_converted_papers(self):
        d = fixture_claimset()
        d["paper"]["origin"] = "converted"      # invalid otherwise; only the scope of W20 is tested
        self.assertEqual(warned(validate(d), "W20"), {})

    def test_w21_pinned_commit_is_not_warned(self):
        d = fixture_claimset()
        a9 = next(a for a in d["artefacts"] if a["id"] == "A9")
        a9["uri"] = "https://github.com/openforecast-org/smooth/blob/72c889476f3488f384c13122a7185458553a9231/.claude/lab/lab.jsonl"
        self.assertEqual(warned(validate(d), "W21"), {})

    def test_archive_uri(self):
        d = example()
        d["artefacts"][0]["archive_uri"] = "swh:1:dir:d198bc9d7a6bcf6db04f476d29314f157507d505"
        self.assertEqual(validate(d).errors, [])
        d["artefacts"][0]["archive_uri"] = ""
        self.assertEqual([e["rule"] for e in validate(d).errors], ["1"])

    def test_rule_58_from_exploration(self):
        d = example("submission-from-lab.json")
        hyp = d["paper"]["exploration"]["hypotheses"]
        hyp["from_exploration"] = hyp["total"]
        self.assertEqual(validate(d).errors, [])
        for bad in (hyp["total"] + 1, -1, 1.5, "2"):
            hyp["from_exploration"] = bad
            self.assertEqual([e["rule"] for e in validate(d).errors], ["58"], bad)

    def test_rule_58_in_covers(self):
        d = fixture_claimset()
        d["paper"]["exploration"]["hypotheses"]["from_exploration"] = 6     # 6 of 6 in the fixture's lab record
        self.assertEqual(validate(d).errors, [])
        d["paper"]["exploration"]["hypotheses"]["from_exploration"] = 7
        self.assertEqual([e["rule"] for e in validate(d).errors], ["58"])


class TestFixtureLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = fixture_state()

    def decided(self, plan):
        return self.st.deciding_runs(plan)

    def test_l1_rn7_reproduces_rn3(self):
        pv, runs = self.decided("PL1")
        self.assertEqual([r["id"] for r in runs], ["RN7"])
        pairs = reproduced_results(self.st, pv, runs)
        self.assertIn(("RS51", "RS35"), pairs)      # ETS(A,N,A): 1482 of 3042 in both
        self.assertEqual({self.st.get(p)["body"]["run"] for _, p in pairs}, {"RN3"})
        self.assertIn("reproduces_prompting_result", plan_flags(self.st, pv, runs))

    def test_l1_not_raised_when_the_results_differ_or_are_zero(self):
        for plan in ("PL2", "PL3", "PL4", "PL5", "PL6"):
            pv, runs = self.decided(plan)
            self.assertNotIn("reproduces_prompting_result", plan_flags(self.st, pv, runs), plan)

    def test_l1_new_data_warning(self):
        body = self.st.get("PL1")["body"]
        self.assertTrue(any("--new-data" in w and "RN2, RN3" in w for w in plan_warnings(self.st, body)))
        self.assertFalse(any("--new-data" in w for w in plan_warnings(self.st, dict(body, new_data="seed 2026"))))

    def test_l2_exclusion_like_parameter(self):
        body = self.st.get("PL1")["body"]
        self.assertTrue(any("edge_band" in w for w in plan_warnings(self.st, body)))
        declared = dict(body, exclusions=[{"name": "edge", "rule": "reference within 1e-8 of 1"}])
        self.assertFalse(any("edge_band" in w for w in plan_warnings(self.st, declared)))

    def test_l2_exclusion_without_a_count(self):
        pv, runs = self.decided("PL1")
        pv = dict(pv, body=dict(pv["body"], exclusions=[{"name": "edge", "rule": "reference within 1e-8 of 1"}]))
        self.assertEqual(uncounted_exclusions(self.st, pv, runs), ["edge"])

    def test_l3_rn8_repeats_rn7(self):
        self.assertEqual(duplicate_runs(self.st, self.st.get("RN8")), ["RN7"])
        self.assertEqual(duplicate_runs(self.st, self.st.get("RN9")), [])

    def test_verify_notes(self):
        rep = verify(Lab(FIXTURE / "lab"), check_files=False)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertIn("plan PL1: reproduces prompting result", rep["notes"])
        self.assertIn("run RN8 repeats RN7: same command, code version, seeds and parameters under another plan",
                      rep["notes"])
        self.assertFalse(any("RN7 repeats" in n for n in rep["notes"]))


SCRIPT = """
import json, os, sys
d = float(sys.argv[1])
json.dump([{"quantity": "mean difference", "value": d, "interval": {"level": 0.95, "lower": d - 0.05, "upper": d + 0.05}},
           {"quantity": "p-value", "value": round(d / 10, 6)}], open(os.environ["CLAIMSET_RESULTS"], "w"))
"""


class TestLabChecks(RuleCase):
    """Valid and broken cases of the lab checks on records built here."""

    def setUp(self):
        super().setUp()
        self.script.write_text(SCRIPT)

    def go(self, d, plan=None, seed=None):
        out = run_command(self.lab, self.agent, [sys.executable, str(self.script), str(d)], plan=plan,
                          seeds=[seed] if seed is not None else None, echo=False, cwd=self.tmp.name)
        return out["run"]["id"], [r["id"] for r in out["results"]]

    def explored(self, d):
        rn, _ = self.go(d)
        return rn, self.hyp("Y is larger.", origin="from_exploration", prompted_by=[rn])

    def flags_of(self, pl):
        st = self.lab.state()
        pv, runs = st.deciding_runs(pl)
        return plan_flags(st, pv, runs)

    def test_reproduced_result_is_flagged_and_exported(self):
        rn, h = self.explored(0.1)
        pl = self.add("plan", {"kind": "confirmatory", "hypotheses": [h], "design": "d", "analysis": "a",
                               "decision": FREQ, "new_data": "none"})
        _, rs = self.go(0.1, plan=pl)
        self.assertEqual(self.flags_of(pl), ["reproduces_prompting_result"])
        self.add("status", {"target": h, "to": "supported", "rationale": "r", "evidence": rs, "plan": pl})
        self.add("status", {"target": self.h, "to": "abandoned", "rationale": "r"})
        out = export(self.lab)
        claim = next(c for c in out["draft"]["claims"] if c.get("hypothesis", {}).get("lab_event") == h)
        self.assertEqual(claim["test"]["flags"], ["reproduces_prompting_result"])
        self.assertEqual(out["draft"]["paper"]["exploration"]["hypotheses"]["from_exploration"], 1)
        self.assertFalse([e for e in validate(out["draft"]).errors if e["rule"] in ("47", "58")])

    def test_new_result_is_not_flagged(self):
        rn, h = self.explored(0.1)
        pl = self.add("plan", {"kind": "confirmatory", "hypotheses": [h], "design": "d", "analysis": "a",
                               "decision": FREQ})
        self.go(0.12, plan=pl)
        self.assertEqual(self.flags_of(pl), [])

    def test_plan_fields_are_checked(self):
        self.assertRefused("schema", "plan", {"kind": "exploratory", "design": "d", "analysis": "a", "new_data": ""})
        for bad in ([{"name": "edge"}], {"edge": "r"}, [{"name": "e", "rule": "r"}, {"name": "e", "rule": "s"}]):
            self.assertRefused("schema", "plan", {"kind": "exploratory", "design": "d", "analysis": "a",
                                                  "exclusions": bad})
        self.add("plan", {"kind": "exploratory", "design": "d", "analysis": "a", "new_data": "new seeds",
                          "exclusions": [{"name": "edge", "rule": "near the boundary"}]})

    def test_counted_exclusion(self):
        rule = dict(FREQ, parameters=dict(FREQ["parameters"], edge_band=1e-8))
        pl = self.plan(rule=rule, exclusions=[{"name": "edge", "rule": "within edge_band of 1"}])
        st = self.lab.state()
        self.assertFalse(any("exclusion" in w for w in plan_warnings(st, st.get(pl)["body"])))
        self.go(0.1, plan=pl)
        st = self.lab.state()
        pv, runs = st.deciding_runs(pl)
        self.assertEqual(uncounted_exclusions(st, pv, runs), ["edge"])
        self.script.write_text(SCRIPT.replace('"value": round(d / 10, 6)}',
                                              '"value": round(d / 10, 6)}, '
                                              '{"quantity": "models excluded by edge", "value": 3}'))
        pl2 = self.plan(rule=rule, exclusions=[{"name": "edge", "rule": "within edge_band of 1"}])
        self.go(0.1, plan=pl2)
        st = self.lab.state()
        pv, runs = st.deciding_runs(pl2)
        self.assertEqual(uncounted_exclusions(st, pv, runs), [])

    def test_duplicate_runs_need_the_same_seeds(self):
        import subprocess
        for args in (["init", "-q"], ["add", "exp.py"], ["commit", "-qm", "code"]):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.tmp.name,
                           check=True, capture_output=True)
        h2 = self.hyp("Z is larger.")
        pl1, pl2, pl3 = self.plan(), self.plan(hypotheses=[h2]), self.plan(hypotheses=[h2])
        rn1, _ = self.go(0.1, plan=pl1, seed=1)
        rn2, _ = self.go(0.1, plan=pl2, seed=2)
        st = self.lab.state()
        self.assertIsNotNone(st.get(rn1)["body"].get("code_version"))
        self.assertEqual(duplicate_runs(st, st.get(rn2)), [])
        rn3, _ = self.go(0.1, plan=pl3, seed=1)
        st = self.lab.state()
        self.assertEqual(duplicate_runs(st, st.get(rn3)), [rn1])


class TestCliWarnings(TestCli):
    def setup_lab(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        self.run_cli("lab", "actor", "--kind", "agent", "--name", "A", "--model",
                     "Anthropic|Claude Opus|5.5|claude-opus-5-5", "--by", "AC1")
        self.run_cli("lab", "question", "Does X help?", "--by", "AC2")
        self.run_cli("lab", "note", "pilot", "--by", "AC2")
        self.run_cli("lab", "hypothesis", "X helps.", "--question", "Q1", "--from-exploration",
                     "--prompted-by", "NT1", "--by", "AC2")

    def plan(self, *extra):
        return self.run_cli("lab", "plan", "--kind", "confirmatory", "--hypotheses", "H1", "--design", "d",
                            "--analysis", "a", "--framework", "deterministic", "--parameter", "edge_band=1e-8",
                            "--quantity", "d=mean difference", "--supported", "d > 0", "--not-supported", "d < 0",
                            "--by", "AC2", *extra)

    def test_plan_warns(self):
        self.setup_lab()
        code, out, err = self.plan()
        self.assertEqual((code, out), (0, "PL1"))
        self.assertIn("--new-data", err)
        self.assertIn("edge_band", err)
        code, out, err = self.plan("--new-data", "seeds 100-199", "--exclusion", "edge=within 1e-8 of 1")
        self.assertEqual((code, out, err), (0, "PL2", ""))
        body = Lab(Path(self.tmp.name) / "lab").state().get("PL2")["body"]
        self.assertEqual(body["new_data"], "seeds 100-199")
        self.assertEqual(body["exclusions"], [{"name": "edge", "rule": "within 1e-8 of 1"}])
        code, _, err = self.plan("--exclusion", "no rule")
        self.assertEqual(code, 1)
        self.assertIn("--exclusion", err)


if __name__ == "__main__":
    unittest.main()
