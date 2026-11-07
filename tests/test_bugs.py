"""Bugs fixed in the validator of every version (current and frozen): a value of the wrong type is reported, never a
crash (rule 86 as the last resort), and a percentage in a statement matches a value recorded in percent."""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claimset import validate as current  # noqa: E402
from claimset.validate import validate  # noqa: E402

COMPAT = ROOT / "tests" / "compat"
VERSIONS = sorted(p.name for p in COMPAT.iterdir() if p.is_dir())


def load(version, name="submission-native.json"):
    return json.loads((COMPAT / version / name).read_text(encoding="utf-8"))


def rules(report):
    return sorted({e["rule"] for e in report.errors})


class TestWrongTypes(unittest.TestCase):
    def test_a_list_of_mitigation_evidence_is_rule_11(self):
        for v in VERSIONS:
            d = load(v)
            lim = d["limitations"][0]
            lim["mitigation"], lim["mitigation_evidence"] = "partial", ["E1"]
            r = validate(d)
            self.assertEqual(rules(r), ["11"], v)
            self.assertIn("one ID", r.errors[0]["message"])

    def test_lists_and_objects_where_ids_belong_are_reported(self):
        for v in VERSIONS:
            for path, value in ((("definitions", 0, "id"), ["D1"]), (("evidence", 0, "id"), {"id": "E1"}),
                                (("claims", 0, "support", 0, "kind"), ["argument"]),
                                (("definitions", 0, "kind"), ["method"])):
                d = load(v)
                x = d
                for k in path[:-1]:
                    x = x[k]
                x[path[-1]] = value
                r = validate(d)
                self.assertTrue(r.errors, (v, path))
                self.assertNotIn("86", rules(r), (v, path))       # a rule of the field names it

    def test_rule_86_when_nothing_else_does(self):
        with mock.patch.object(current, "_validate", side_effect=TypeError("unhashable type: 'list'")):
            r = validate({})
        self.assertEqual(rules(r), ["86"])
        self.assertIn("unhashable type", r.errors[0]["message"])


class TestPercentages(unittest.TestCase):
    def test_a_percentage_matches_a_value_in_percent_or_as_a_fraction(self):
        for v in VERSIONS:
            for value in (76.7, 0.767):
                d = load(v)
                c = next(c for c in d["claims"] if c["role"] == "principal")
                e = d["evidence"][0]
                e["results"] = [{"id": "R1", "variable": "D1", "value": value, "unit": "%" if value > 1 else "1"}]
                c["support"] = [{"kind": "empirical_quant", "evidence": e["id"], "results": [f"{e['id']}.R1"]}]
                c["statement"] = "[[D2:Method M]] improves [[D1:forecast skill]] by 76.7% over the [[D3:seasonal " \
                                 "naive method]] on the [[D4:UK monthly retail sales]]."
                w = {x["rule"] for x in validate(d).warnings}
                self.assertNotIn("W12", w, (v, value))
                self.assertNotIn("W17", w, (v, value))

    def test_a_different_number_still_warns(self):
        self.assertFalse(current.number_matches(7.67, "by 76.7%"))
        self.assertFalse(current.number_matches(0.77, "by 76.7%"))


if __name__ == "__main__":
    unittest.main()
