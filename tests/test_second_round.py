"""Core additions from the second round of field trials (spec v0.39): statistics and approximate values (rule 87),
units and targets of definitions and the kind process (rule 88), and their reading view."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claimset.render import render  # noqa: E402
from claimset.validate import ENUM, validate  # noqa: E402

NATIVE = json.loads((ROOT / "examples" / "submission-native.json").read_text(encoding="utf-8"))


def doc():
    return copy.deepcopy(NATIVE)


def rules(report):
    return sorted({e["rule"] for e in report.errors})


def defn(d, did):
    return next(x for x in d["definitions"] if x["id"] == did)


def with_result(**extra):
    d = doc()
    res = {"id": "R1", "variable": "D1", "value": 1.5, "conditions": {"comparator": "D3"}}
    res.update(extra)
    d["evidence"][0]["results"] = [res]
    return d


class TestRule87(unittest.TestCase):
    def test_statistic_and_approximate_on_results(self):
        d = with_result(statistic="ratio", approximate=True)
        self.assertEqual(validate(d).errors, [])
        self.assertIn("E1.R1 D1 (ratio) = about 1.5", render(d))
        for bad in ({"statistic": "Cohen's d"}, {"statistic": ["mean"]}, {"approximate": False},
                    {"approximate": "yes"}):
            self.assertEqual(rules(validate(with_result(**bad))), ["87"], bad)

    def test_on_table_columns(self):
        d = doc()
        d["evidence"][0]["tables"] = [{"id": "T1", "description": "Half-times relative to mock", "row_labels": ["kd"],
                                       "columns": [{"variable": "half-time", "statistic": "ratio", "approximate": True}],
                                       "rows": [{"id": "r1", "label": ["RAC2"], "values": [2.0]}]}]
        self.assertEqual(validate(d).errors, [])
        d["evidence"][0]["tables"][0]["columns"][0]["statistic"] = "fold change"
        self.assertEqual(rules(validate(d)), ["87"])

    def test_the_list(self):
        self.assertIn("standardised_mean_difference", ENUM["statistic"])
        self.assertIn("nnt", ENUM["statistic"])


class TestRule88(unittest.TestCase):
    def test_unit(self):
        d = doc()
        defn(d, "D1")["unit"] = "1"
        self.assertEqual(validate(d).errors, [])
        self.assertIn("[unit 1]", render(d))
        defn(d, "D1")["unit"] = ""
        self.assertEqual(rules(validate(d)), ["88"])
        d = doc()
        defn(d, "D2")["unit"] = "GeV"                       # a method has no unit
        self.assertEqual(rules(validate(d)), ["88"])

    def test_targets_and_process(self):
        d = doc()
        d["definitions"] += [
            {"id": "D9", "term": "knockdown of RAC2", "kind": "intervention", "definition": "siRNA against RAC2.",
             "targets": ["D10"]},
            {"id": "D10", "term": "RAC2", "kind": "entity", "definition": "The gene RAC2."},
            {"id": "D11", "term": "1,5-hydrogen atom transfer", "kind": "process", "definition": "A radical step."}]
        self.assertEqual(validate(d).errors, [])
        self.assertIn("[targets D10]", render(d))
        defn(d, "D9")["targets"] = ["D10", "D11"]             # a process may be targeted too
        self.assertEqual(validate(d).errors, [])
        defn(d, "D9")["targets"] = ["D2"]                     # a method is not a target
        self.assertEqual(rules(validate(d)), ["88"])
        defn(d, "D9")["targets"] = []
        self.assertEqual(rules(validate(d)), ["88"])
        d2 = copy.deepcopy(d)
        defn(d2, "D9")["targets"] = ["D10"]
        defn(d2, "D10")["targets"] = ["D11"]                  # only an intervention targets
        self.assertEqual(rules(validate(d2)), ["88"])


if __name__ == "__main__":
    unittest.main()
