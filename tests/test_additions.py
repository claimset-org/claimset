"""Smaller additions of spec v0.39: collaborations (rule 85), funding kind and role (rule 69), locations and
constructs (rule 83), the mechanistic type and novelty not_applicable (rule 84), the relation disputes_evidence,
the analytic approach source_criticism, controls on a group's dimension (rule 66) and the warrant of an argument
(rule 6)."""

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
    return copy.deepcopy(NATIVE)


def rules(report):
    return sorted({e["rule"] for e in report.errors})


def principal(d):
    return next(c for c in d["claims"] if c["role"] == "principal")


def defn(d, did):
    return next(x for x in d["definitions"] if x["id"] == did)


class TestCollaborations(unittest.TestCase):
    def test_rule_85(self):
        d = doc()
        d["paper"]["collaborations"] = [{"name": "ATLAS Collaboration", "identifiers": ["ror:000000000"]}]
        self.assertEqual(validate(d).errors, [])
        self.assertIn("on behalf of ATLAS Collaboration ror:000000000", render(d))
        for bad in ([], [{"identifiers": ["ror:x"]}], [{"name": "A", "members": 3000}], [{"name": "A", "identifiers": []}],
                    [{"name": "A", "identifiers": ["not an id"]}], "ATLAS"):
            d["paper"]["collaborations"] = bad
            self.assertEqual(rules(validate(d)), ["85"], bad)


class TestFunding(unittest.TestCase):
    def test_kind_and_role(self):
        d = doc()
        d["paper"]["declarations"]["funding"] = [
            {"funder": "A supermarket chain", "kind": "in_kind", "role": "Supplied food vouchers; no other role."}]
        self.assertEqual(validate(d).errors, [])
        self.assertIn("A supermarket chain in kind (role: Supplied food vouchers; no other role.)", render(d))
        for bad in ({"funder": "F", "kind": "money"}, {"funder": "F", "role": ""}, {"funder": "F", "amount": 10}):
            d["paper"]["declarations"]["funding"] = [bad]
            self.assertEqual(rules(validate(d)), ["69"], bad)


class TestRule83(unittest.TestCase):
    def setting(self, d, **extra):
        d["definitions"].append(dict({"id": "D9", "term": "the reserves", "kind": "setting",
                                      "definition": "63 nature reserves in Germany."}, **extra))

    def test_location(self):
        d = doc()
        self.setting(d, location=[{"latitude": 51.43, "longitude": 6.62, "label": "trap 1"}, {"latitude": -33.9,
                                                                                               "longitude": 151.2}])
        self.assertEqual(validate(d).errors, [])
        self.assertIn("[location 51.43, 6.62 trap 1; -33.9, 151.2]", render(d))
        for bad in ([], [{"latitude": 91, "longitude": 0}], [{"latitude": 0, "longitude": 181}],
                    [{"lat": 1, "lon": 2}], [{"latitude": "51N", "longitude": 6}], [{"latitude": 1, "longitude": 2,
                                                                                    "label": ""}]):
            d = doc()
            self.setting(d, location=bad)
            self.assertEqual(rules(validate(d)), ["83"], bad)

    def test_location_only_on_settings(self):
        d = doc()
        defn(d, "D4")["location"] = [{"latitude": 51.5, "longitude": -0.1}]
        self.assertEqual(rules(validate(d)), ["83"])

    def test_measures(self):
        d = doc()
        d["definitions"].append({"id": "D9", "term": "forecastability", "kind": "concept", "definition": "How "
                                 "predictable a series is."})
        defn(d, "D1")["measures"] = ["D9"]
        self.assertEqual(validate(d).errors, [])
        self.assertIn("[measures D9]", render(d))
        defn(d, "D1")["measures"] = ["D2"]                    # a method, not a concept
        self.assertEqual(rules(validate(d)), ["83"])
        defn(d, "D1")["measures"] = []
        self.assertEqual(rules(validate(d)), ["83"])
        d = doc()
        d["definitions"].append({"id": "D9", "term": "x", "kind": "concept", "definition": "x"})
        defn(d, "D2")["measures"] = ["D9"]                    # only a variable measures
        self.assertEqual(rules(validate(d)), ["83"])


class TestClaims(unittest.TestCase):
    def test_mechanistic(self):
        d = doc()
        principal(d)["type"] = "mechanistic"
        self.assertEqual(validate(d).errors, [])

    def test_rule_84_not_applicable(self):
        d = doc()
        principal(d)["novelty"] = "not_applicable"
        self.assertEqual(rules(validate(d)), ["84"])

    def test_disputes_evidence_within_the_paper_and_outside(self):
        d = doc()
        c = principal(d)
        c["relations"] = [{"type": "disputes_evidence", "target": "10.1086/ahr/106.2.343", "id_type": "doi"}]
        self.assertEqual(validate(d).errors, [])

    def test_source_criticism(self):
        from claimset.validate import ENUM
        self.assertIn("source_criticism", ENUM["analytic_approach"])


class TestControlOnADimension(unittest.TestCase):
    def test_a_control_of_the_groups_kind(self):
        d = doc()
        d["definitions"] += [
            {"id": "D9", "term": "the variants", "kind": "method", "definition": "M1 and M2.", "members": ["D10", "D11"]},
            {"id": "D10", "term": "M1", "kind": "method", "definition": "Variant 1."},
            {"id": "D11", "term": "M2", "kind": "method", "definition": "Variant 2."}]
        d["evidence"][0]["setup"]["studied"] = ["D2", "D3", "D9"]
        d["evidence"][0]["results"] = [{"id": "R1", "variable": "D1", "value": 0.1, "conditions": {"D9": "D3"}}]
        self.assertEqual(validate(d).errors, [])               # the seasonal naive method, a method, as a control
        d["evidence"][0]["results"][0]["conditions"] = {"D9": "D4"}   # data on a dimension of methods
        self.assertEqual(rules(validate(d)), ["66"])


class TestWarrantOnce(unittest.TestCase):
    def test_rule_6_accepts_the_argument_s_warrant(self):
        d = doc()
        d["warrants"] = [{"id": "W1", "reasoning": "interpretive", "premises": [],
                          "text": "Seasonality shows in the error pattern."}]
        c = next(c for c in d["claims"] if c["id"] == "C1")
        c2 = copy.deepcopy(principal(d))
        c2.update({"id": "C9", "role": "secondary", "type": "interpretive", "novelty": "not_applicable",
                   "support": [{"kind": "argument", "warrant": "W1"}],
                   "statement": "The gain of [[D2:method M]] reflects the seasonality of [[D4:UK monthly retail sales]]."})
        for k in ("about", "strength", "limitations", "scope"):
            c2.pop(k, None)
        d["claims"].append(c2)
        self.assertNotIn("6", rules(validate(d)))
        c2["support"] = [{"kind": "argument"}]
        self.assertIn("6", rules(validate(d)))
        del c


if __name__ == "__main__":
    unittest.main()
