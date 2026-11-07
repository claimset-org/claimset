"""Limits and ranges as values (spec v0.39 §3.4): rule 66 and rule 48 as revised, quantities that read them
(rule 47), rule 82 (no interval or uncertainty on them), the lab's reading of them and the reading view."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claimset.lab import decision  # noqa: E402
from claimset.render import render  # noqa: E402
from claimset.validate import validate  # noqa: E402

NATIVE = json.loads((ROOT / "examples" / "submission-native.json").read_text(encoding="utf-8"))
FROM_LAB = json.loads((ROOT / "examples" / "submission-from-lab.json").read_text(encoding="utf-8"))

LIMIT = {"bound": "upper", "limit": 0.5, "level": 0.95}
RANGE = {"min": 111.4, "max": 116.6, "level": 0.95}


def rules(report):
    return sorted({e["rule"] for e in report.errors})


def with_value(value, **extra):
    d = copy.deepcopy(NATIVE)
    res = {"id": "R1", "variable": "upper limit on the cross-section", "value": value, "unit": "pb"}
    res.update(extra)
    d["evidence"][0]["results"] = [res]
    return d


class TestRule66(unittest.TestCase):
    def test_limits_and_ranges_are_values(self):
        for v in (LIMIT, {"bound": "lower", "limit": 3}, RANGE, {"min": 36, "max": 77}, {"min": 5, "max": 5}):
            self.assertEqual(validate(with_value(v)).errors, [], v)

    def test_bad_limits_and_ranges(self):
        for v in ({"bound": "below", "limit": 1}, {"bound": "upper"}, {"bound": "upper", "limit": "0.5"},
                  {"bound": "upper", "limit": 1, "level": 95}, {"bound": "upper", "limit": 1, "kind": "CLs"},
                  {"min": 2, "max": 1}, {"min": 1}, {"min": 1, "max": 2, "mean": 1.5},
                  {"min": 1, "max": 2, "level": 0}):
            r = validate(with_value(v))
            self.assertEqual(rules(r), ["66"], v)
            self.assertIn("limit", r.errors[0]["message"])

    def test_in_a_table(self):
        d = copy.deepcopy(NATIVE)
        d["evidence"][0]["tables"] = [{"id": "T1", "description": "Yields by substrate class", "row_labels": ["class"],
                                       "columns": ["yield"], "units": ["%"],
                                       "rows": [{"id": "r1", "label": ["oxalates"], "values": [{"min": 36, "max": 77}]},
                                                {"id": "r2", "label": ["alkynes"], "values": [{"min": 2, "max": 1}]}]}]
        self.assertEqual(rules(validate(d)), ["48"])
        d["evidence"][0]["tables"][0]["rows"][1]["values"] = [{"bound": "lower", "limit": 45}]
        self.assertEqual(validate(d).errors, [])


class TestNoIntervalOnALimit(unittest.TestCase):
    def test_rule_82(self):
        d = with_value(LIMIT, uncertainty=[{"kind": "se", "value": 0.1}])
        self.assertEqual(rules(validate(d)), ["82"])
        d = with_value(RANGE, interval={"level": 0.95, "lower": 110, "upper": 118})
        self.assertEqual(rules(validate(d)), ["82"])


class TestQuantities(unittest.TestCase):
    def test_rule_47_accepts_the_new_fields(self):
        d = copy.deepcopy(FROM_LAB)
        c = next(c for c in d["claims"] if c.get("test", {}).get("quantities"))
        q = c["test"]["quantities"][next(iter(c["test"]["quantities"]))]
        for f in ("value.limit", "value.min", "value.max"):
            q["field"] = f
            self.assertNotIn("47", rules(validate(d)), f)
        q["field"] = "value.mean"
        self.assertIn("47", rules(validate(d)))

    def test_the_lab_reads_them(self):
        self.assertEqual(decision.read_field({"value": LIMIT}, "value.limit"), 0.5)
        self.assertEqual(decision.read_field({"value": RANGE}, "value.max"), 116.6)
        self.assertIsNone(decision.read_field({"value": 3.0}, "value.limit"))


class TestReadingView(unittest.TestCase):
    def test_shown_as_bounds_and_spans(self):
        self.assertIn("E1.R1 upper limit on the cross-section = < 0.5 (upper limit, 95%) pb", render(with_value(LIMIT)))
        self.assertIn("= 111.4 to 116.6 (range, 95%) pb", render(with_value(RANGE)))


if __name__ == "__main__":
    unittest.main()
