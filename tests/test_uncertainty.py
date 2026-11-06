"""Typed uncertainty (spec v0.39 §3.4): rule 82, rule 47 as revised (a quantity's field), warnings 17 and 35, the
reading view, and results with uncertainty in the lab record."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from claimset.lab import decision  # noqa: E402
from claimset.render import render  # noqa: E402
from claimset.validate import QUANTITY_FIELDS, validate  # noqa: E402

NATIVE = json.loads((ROOT / "examples" / "submission-native.json").read_text(encoding="utf-8"))
FROM_LAB = json.loads((ROOT / "examples" / "submission-from-lab.json").read_text(encoding="utf-8"))


def rules(report):
    return sorted({e["rule"] for e in report.errors})


def warnings(report):
    return sorted({w["rule"] for w in report.warnings})


def with_result(extra):
    """The native example with one result in E1 whose value is 126.0 GeV."""
    d = copy.deepcopy(NATIVE)
    res = {"id": "R1", "variable": "mass of the new boson", "value": 126.0, "unit": "GeV"}
    res.update(extra)
    d["evidence"][0]["results"] = [res]
    return d, res


class TestRule82(unittest.TestCase):
    def test_components_and_kinds(self):
        d, _ = with_result({"uncertainty": [{"kind": "stat", "value": 0.4},
                                            {"kind": "sys", "value": 0.3, "label": "energy scale"},
                                            {"kind": "sys", "plus": 0.2, "minus": 0.1, "label": "theory"}],
                            "interval": {"level": 0.683, "lower": 125.5, "upper": 126.5, "kind": "confidence"}})
        r = validate(d)
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])

    def test_bad_uncertainty(self):
        for bad in ([], [{"kind": "sem", "value": 1}], [{"kind": "se"}], [{"kind": "se", "value": -1}],
                    [{"kind": "se", "value": 1, "plus": 1, "minus": 1}], [{"kind": "sys", "plus": 1}],
                    [{"kind": "stat", "value": 1, "level": 95}], [{"kind": "sd", "value": 1, "note": "x"}],
                    [{"kind": "sys", "value": 1}, {"kind": "sys", "value": 2}], {"kind": "se", "value": 1}):
            d, _ = with_result({"uncertainty": bad})
            self.assertEqual(rules(validate(d)), ["82"], bad)

    def test_bad_interval(self):
        for bad in ({"level": 0.95, "lower": 2, "upper": 1}, {"level": 95, "lower": 1, "upper": 2},
                    {"lower": 1, "upper": 2, "kind": "bayesian"}, {"lower": 1, "upper": 2, "width": 1},
                    {"level": 0.95, "lower": 1}):
            d, _ = with_result({"interval": bad})
            self.assertEqual(rules(validate(d)), ["82"], bad)

    def test_only_with_a_numeric_value(self):
        d, res = with_result({"uncertainty": [{"kind": "se", "value": 1}]})
        res["value"] = "high"
        self.assertEqual(rules(validate(d)), ["82"])

    def test_on_the_estimate(self):
        d = copy.deepcopy(NATIVE)
        d["evidence"][0]["uncertainty"] = [{"kind": "se", "value": 0.015}]
        self.assertEqual(validate(d).errors, [])
        d["evidence"][0]["uncertainty"] = [{"kind": "se"}]
        self.assertEqual(rules(validate(d)), ["82"])

    def test_a_column_of_uncertainties(self):
        d = copy.deepcopy(NATIVE)
        d["evidence"][0]["tables"] = [{"id": "T1", "description": "Mean MASE by method", "row_labels": ["method"],
                                       "columns": ["mean MASE", {"variable": "mean MASE",
                                                                 "conditions": {"uncertainty": "sd"}}],
                                       "rows": [{"id": "r1", "label": ["M"], "values": [0.91, 0.12]}]}]
        self.assertEqual(validate(d).errors, [])
        d["evidence"][0]["tables"][0]["columns"][1]["conditions"]["uncertainty"] = "standard deviation"
        self.assertEqual(rules(validate(d)), ["82"])


class TestWarning35(unittest.TestCase):
    def test_native_papers_say_what_kind(self):
        d, _ = with_result({"uncertainty": [{"kind": "unspecified", "value": 0.4}],
                            "interval": {"level": 0.95, "lower": 125, "upper": 127}})
        r = validate(d)
        self.assertEqual(r.errors, [])
        self.assertEqual(warnings(r), ["W35"])
        self.assertEqual(len([w for w in r.warnings if w["rule"] == "W35"]), 2)

    def test_converted_papers_may_not_know(self):
        d, _ = with_result({"uncertainty": [{"kind": "unspecified", "value": 0.4}]})
        d["paper"]["origin"] = "converted"
        self.assertNotIn("W35", warnings(validate(d)))


class TestStatementNumbers(unittest.TestCase):
    def test_warning_17_finds_the_uncertainty(self):
        d, _ = with_result({"uncertainty": [{"kind": "stat", "value": 0.4}, {"kind": "sys", "value": 0.3,
                                                                              "label": "energy scale"}]})
        c = next(c for c in d["claims"] if c["role"] == "principal")
        c["statement"] = "The mass of the new boson is 126.0 GeV, ± 0.4 (stat) ± 0.3 (sys)."
        c["support"] = [{"kind": "empirical_quant", "evidence": "E1", "results": ["E1.R1"]}]
        self.assertNotIn("W17", warnings(validate(d)))


class TestQuantityField(unittest.TestCase):
    def test_rule_47_field_from_the_list(self):
        self.assertIn("uncertainty.se", QUANTITY_FIELDS)
        d = copy.deepcopy(FROM_LAB)
        c = next(c for c in d["claims"] if c.get("test", {}).get("quantities"))
        name = next(iter(c["test"]["quantities"]))
        c["test"]["quantities"][name]["field"] = "uncertainty.se"
        self.assertNotIn("47", rules(validate(d)))
        c["test"]["quantities"][name]["field"] = "interval.middle"
        self.assertIn("47", rules(validate(d)))

    def test_the_lab_shares_the_list_and_reads_a_component(self):
        self.assertIs(decision.FIELDS, QUANTITY_FIELDS)
        r = {"value": 0.3, "uncertainty": [{"kind": "se", "value": 0.1}, {"kind": "sys", "plus": 1, "minus": 2}]}
        self.assertEqual(decision.read_field(r, "uncertainty.se"), 0.1)
        self.assertIsNone(decision.read_field(r, "uncertainty.sys"))     # asymmetric: no single number
        self.assertIsNone(decision.read_field(r, "uncertainty.sd"))
        self.assertEqual(decision.read_field(r, "value"), 0.3)


class TestLabResults(unittest.TestCase):
    def test_results_files_may_carry_uncertainty(self):
        import tempfile
        from claimset.lab.run import _parse_results
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "results.json"
            f.write_text(json.dumps([{"variable": "a", "value": 1.0, "uncertainty": [{"kind": "se", "value": 0.1}]},
                                     {"variable": "b", "value": 2.0, "uncertainty": [{"kind": "se"}]}]))
            good, problems = _parse_results(f)
        self.assertEqual(good, [{"variable": "a", "value": 1.0, "uncertainty": [{"kind": "se", "value": 0.1}]}])
        self.assertEqual(len(problems), 1)
        self.assertIn("entry 1", problems[0])


class TestReadingView(unittest.TestCase):
    def test_uncertainty_and_interval_kind_are_shown(self):
        d, _ = with_result({"uncertainty": [{"kind": "stat", "value": 0.4},
                                            {"kind": "sys", "plus": 0.2, "minus": 0.1, "label": "theory"}],
                            "interval": {"level": 0.95, "lower": 125, "upper": 127, "kind": "credible"}})
        text = render(d)
        self.assertIn("E1.R1 mass of the new boson = 126 ± 0.4 (stat) +0.2/-0.1 (sys, theory) GeV "
                      "95% credible interval 125 to 127", text)


if __name__ == "__main__":
    unittest.main()
