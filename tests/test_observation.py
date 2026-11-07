"""Observation evidence (spec v0.39 §3.4, *Profile observation*; §4 `empirical_obs`): rule 89, rule 10 for the new
support kind, warnings 37 and 8, and the reading view."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claimset.render import render  # noqa: E402
from claimset.validate import validate  # noqa: E402

NATIVE = json.loads((ROOT / "examples" / "submission-native.json").read_text(encoding="utf-8"))


def doc():
    """The native example with a blot (A9, a specimen figure) as observation evidence E9 supporting claim C9."""
    d = copy.deepcopy(NATIVE)
    d["artefacts"].append({"id": "A9", "kind": "figure", "function": "specimen", "uri": "https://example.org/blot.png",
                           "licence": "CC-BY-4.0", "availability": {"level": "public"},
                           "description": "Western blot of RAC2 after knockdown, with a loading control."})
    d["definitions"].append({"id": "D9", "term": "knockdown of RAC2", "kind": "intervention",
                             "definition": "siRNA against RAC2."})
    d["evidence"].append({"id": "E9", "profile": "observation", "data_origin": "primary", "preregistered": False,
                          "description": "Western blot after knockdown.", "setup": {"studied": ["D9"]},
                          "records": [{"kind": "image", "artefact": "A9", "description": "The blot"}],
                          "results": [{"id": "R1", "variable": "RAC2 band reduced", "value": True,
                                       "read_from": {"artefact": "A9", "precision": "by eye"}}],
                          "availability": {"level": "public"}})
    d["claims"].append({"id": "C9", "statement": "The [[D9:knockdown of RAC2]] reduced the RAC2 protein.",
                        "about": {"subject": "D9", "relation": "reduces", "object": "the RAC2 protein",
                                  "comparator": None, "conditions": []},
                        "type": "descriptive", "role": "secondary", "novelty": "not_applicable",
                        "support": [{"kind": "empirical_obs", "evidence": "E9", "results": ["E9.R1"]}],
                        "provenance": "author_stated"})
    return d


def rules(report):
    return sorted({e["rule"] for e in report.errors})


def warnings(report):
    return sorted({w["rule"] for w in report.warnings})


def ev(d):
    return next(e for e in d["evidence"] if e["id"] == "E9")


class TestObservation(unittest.TestCase):
    def test_valid(self):
        r = validate(doc())
        self.assertEqual(r.errors, [])
        self.assertNotIn("W37", warnings(r))
        self.assertNotIn("W8", warnings(r))

    def test_rule_89_records(self):
        for bad in ([], None, [{"kind": "blot", "artefact": "A9"}], [{"artefact": "A9"}],
                    [{"kind": "image", "artefact": "A99"}], [{"kind": "image", "artefact": ["A9"]}],
                    [{"kind": "image", "lanes": 4}], [{"kind": "image", "description": ""}], "image"):
            d = doc()
            if bad is None:
                del ev(d)["records"]
            else:
                ev(d)["records"] = bad
            self.assertIn("89", rules(validate(d)), bad)

    def test_rule_89_records_only_on_observation(self):
        d = doc()
        d["evidence"][0]["records"] = [{"kind": "image", "artefact": "A9"}]
        self.assertEqual(rules(validate(d)), ["89"])

    def test_rule_10_empirical_obs_points_to_observation(self):
        d = doc()
        d["claims"][-1]["support"][0]["evidence"] = "E1"          # quant evidence
        self.assertIn("10", rules(validate(d)))
        d = doc()
        d["claims"][-1]["support"][0]["kind"] = "empirical_quant"  # observation evidence
        self.assertIn("10", rules(validate(d)))

    def test_warnings_37_and_8(self):
        d = doc()
        del ev(d)["records"][0]["artefact"]
        self.assertIn("W37", warnings(validate(d)))
        d = doc()
        del ev(d)["results"]
        d["claims"][-1]["support"][0].pop("results")
        self.assertIn("W8", warnings(validate(d)))

    def test_every_record_kind(self):
        for kind in ("image", "spectrum", "structure", "specimen", "recording", "other"):
            d = doc()
            ev(d)["records"][0]["kind"] = kind
            self.assertEqual(validate(d).errors, [], kind)

    def test_reading_view(self):
        text = render(doc())
        self.assertIn("E9 [observation; data_origin primary]", text)
        self.assertIn("  record: image; in A9; The blot", text)


if __name__ == "__main__":
    unittest.main()
