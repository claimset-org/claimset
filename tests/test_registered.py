"""Tests registered outside a lab record (spec v0.39 §3.3.3): rules 42, 44 and 46 as revised, rule 81, warning 34,
the anchor `registry`, the limitation category `deviation`, the migration of a v0.38 registration URI and the
reading view."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claimset.migrate import migrate  # noqa: E402
from claimset.render import render  # noqa: E402
from claimset.validate import validate  # noqa: E402

BASE = json.loads((ROOT / "tests" / "compat" / "0.39" / "submission-registered.json").read_text(encoding="utf-8"))
FROM_LAB = json.loads((ROOT / "examples" / "submission-from-lab.json").read_text(encoding="utf-8"))


def doc():
    d = copy.deepcopy(BASE)
    return d, next(c for c in d["claims"] if c["id"] == "C2")


def rules(report):
    return sorted({e["rule"] for e in report.errors})


def warnings(report):
    return sorted({w["rule"] for w in report.warnings})


class TestRegisteredTest(unittest.TestCase):
    def test_a_registered_test_is_valid_without_a_lab_record(self):
        d, _ = doc()
        r = validate(d)
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])
        self.assertNotIn("exploration", d["paper"])

    def test_a_trial_registry_number_as_a_curie(self):
        d, _ = doc()
        d["references"][0] = {"id": "B1", "target": "clinicaltrials:NCT01234567", "id_type": "curie"}
        self.assertEqual(validate(d).errors, [])

    def test_a_hypothesis_stated_in_the_paper_is_exploratory(self):
        d, c = doc()
        for k in ("planned_at", "planned_at_anchor", "test"):
            del c[k]
        c["planning"] = "exploratory"
        del d["references"]
        c["limitations"].remove("L2")
        d["limitations"] = [x for x in d["limitations"] if x["id"] != "L2"]
        self.assertEqual(validate(d).errors, [])


class TestRule42(unittest.TestCase):
    def test_outcome_and_planning_are_required(self):
        d, c = doc()
        del c["hypothesis"]["outcome"]
        self.assertIn("42", rules(validate(d)))
        d, c = doc()
        del c["planning"], c["planned_at"], c["planned_at_anchor"], c["test"]
        del d["references"]
        self.assertIn("42", rules(validate(d)))

    def test_a_lab_event_is_an_event_id_when_given(self):
        d, c = doc()
        c["hypothesis"]["lab_event"] = ""
        self.assertIn("42", rules(validate(d)))


class TestRules44And46(unittest.TestCase):
    def test_exploration_is_needed_only_for_claims_from_a_lab_record(self):
        d, c = doc()
        c["hypothesis"]["lab_event"] = "H1"
        c["test"]["lab_plan"] = "PL1"
        self.assertIn("44", rules(validate(d)))

    def test_registered_tests_are_not_counted_against_the_exploration(self):
        d = copy.deepcopy(FROM_LAB)
        self.assertEqual(validate(d).errors, [])
        extra = copy.deepcopy(next(c for c in d["claims"] if c.get("test")))
        extra["id"] = "C99"
        extra["role"] = "secondary"
        del extra["hypothesis"]["lab_event"]
        extra["test"].pop("lab_plan", None)
        extra["test"]["registration"] = "B99"
        extra["planned_at_anchor"] = "registry"
        extra.pop("depends_on", None)
        d["claims"].append(extra)
        d.setdefault("references", []).append({"id": "B99", "target": "10.17605/OSF.IO/ABCDE", "id_type": "doi"})
        self.assertEqual(validate(d).errors, [])
        extra["hypothesis"]["lab_event"] = "H99"                 # the same claim from a lab record is counted
        self.assertIn("46", rules(validate(d)))


class TestRule81(unittest.TestCase):
    def test_a_confirmatory_test_names_its_plan(self):
        d, c = doc()
        del c["test"]["registration"]
        c["planned_at_anchor"] = "none"
        del d["references"]
        r = validate(d)
        self.assertEqual(rules(r), ["81"])
        self.assertIn("lab_plan", r.errors[0]["message"])

    def test_the_registration_is_a_reference(self):
        d, c = doc()
        c["test"]["registration"] = "https://doi.org/10.17605/OSF.IO/ABCDE"
        r = validate(d)
        self.assertIn("81", rules(r))
        self.assertIn("51", rules(r))          # B1 is then listed but never cited

    def test_a_registry_anchor_needs_a_registration(self):
        d, c = doc()
        del c["test"]["registration"]
        c["test"]["lab_plan"] = "PL1"
        del d["references"]
        r = validate(d)
        self.assertEqual(rules(r), ["81"])
        self.assertIn("registry", r.errors[0]["message"])

    def test_a_confirmatory_claim_tests_a_hypothesis(self):
        d, c = doc()
        del c["hypothesis"]
        self.assertEqual(rules(validate(d)), ["81"])

    def test_the_registration_counts_as_citing_the_reference(self):
        d, _ = doc()
        self.assertNotIn("51", rules(validate(d)))


class TestDeviations(unittest.TestCase):
    def test_warning_34_without_a_deviation_limitation(self):
        d, c = doc()
        c["limitations"].remove("L2")
        d["limitations"] = [x for x in d["limitations"] if x["id"] != "L2"]
        r = validate(d)
        self.assertEqual(r.errors, [])
        self.assertEqual(warnings(r), ["W34"])

    def test_a_deviation_with_another_category_raises_warning_34(self):
        d, _ = doc()
        next(x for x in d["limitations"] if x["id"] == "L2")["category"] = "other"
        self.assertEqual(warnings(validate(d)), ["W34"])


class TestEarlierVersions(unittest.TestCase):
    def v038(self):
        d, c = doc()
        d["schema_version"] = "0.38"
        c["test"]["registration"] = "https://doi.org/10.17605/OSF.IO/ABCDE"
        c["planned_at_anchor"] = "none"
        del d["references"]
        next(x for x in d["limitations"] if x["id"] == "L2")["category"] = "other"
        return d

    def test_a_v038_file_keeps_its_rules(self):
        d = self.v038()
        self.assertIn("42", rules(validate(d)))          # v0.38 required a lab event
        d["claims"] = [c for c in d["claims"] if c["id"] != "C2"] + [
            {k: v for k, v in next(c for c in d["claims"] if c["id"] == "C2").items()
             if k not in ("hypothesis", "planning", "planned_at", "planned_at_anchor", "test")}]
        self.assertEqual(validate(d).errors, [])

    def test_migrate_turns_the_uri_into_a_reference(self):
        d = self.v038()
        out, done, todo = migrate(d)
        c = next(x for x in out["claims"] if x["id"] == "C2")
        self.assertEqual(c["test"]["registration"], "B1")
        self.assertEqual(out["references"], [{"id": "B1", "target": "10.17605/OSF.IO/ABCDE", "id_type": "doi"}])
        self.assertIn("C2.test.registration: https://doi.org/10.17605/OSF.IO/ABCDE -> reference B1", done)
        self.assertEqual(todo, [])

    def test_migrate_lists_the_archive_of_a_url_registration(self):
        d = self.v038()
        next(c for c in d["claims"] if c["id"] == "C2")["test"]["registration"] = "https://aspredicted.org/x.pdf"
        out, _, todo = migrate(d)
        self.assertEqual(out["references"][0]["id_type"], "url")
        self.assertTrue(any("archive_uri" in t for t in todo))


class TestReadingView(unittest.TestCase):
    def test_the_plan_is_shown(self):
        d, _ = doc()
        self.assertIn("plan: registered B1; planned 2026-01-15T00:00:00Z; anchor registry", render(d))


if __name__ == "__main__":
    unittest.main()
