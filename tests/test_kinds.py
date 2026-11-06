"""The kinds of things added in spec v0.39 (§3.2, *Things and their roles*): rule 63 accepts them, warning 28 counts
them as parts of a study, files before v0.39 keep their kinds, and the lab record accepts them as term kinds."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from claimset.lab import core  # noqa: E402
from claimset.validate import ENUM, validate  # noqa: E402

NATIVE = json.loads((ROOT / "examples" / "submission-native.json").read_text(encoding="utf-8"))
NEW = {"substance": "chebi:16716", "organism": "ncbitaxon:7227", "biological_material": "cellosaurus:CVCL_0031",
       "entity": "hgnc:9802", "source": "wikidata:Q42"}


def rules(report):
    return sorted({e["rule"] for e in report.errors})


def warnings(report):
    return sorted({w["rule"] for w in report.warnings})


def with_term(kind, in_setup):
    """The native example with D9, of `kind`, as the context of its principal claim."""
    d = copy.deepcopy(NATIVE)
    d["definitions"].append({"id": "D9", "term": "the thing", "kind": kind, "definition": "A thing the study names.",
                             "identifiers": [NEW.get(kind, "wikidata:Q42")]})
    c = next(c for c in d["claims"] if c["role"] == "principal")
    c["about"]["conditions"] = list(c["about"].get("conditions") or []) + ["D9"]
    c["statement"] = c["statement"].rstrip(".") + ", for [[D9:the thing]]."
    if in_setup:
        d["evidence"][0]["setup"].setdefault("cases", []).append("D9")
    return d


class TestKinds(unittest.TestCase):
    def test_the_five_kinds_are_valid(self):
        for kind in NEW:
            self.assertIn(kind, ENUM["definition_kind"])
            r = validate(with_term(kind, in_setup=True))
            self.assertEqual(r.errors, [], kind)
            self.assertNotIn("W28", warnings(r), kind)

    def test_warning_28_counts_them_as_parts_of_the_study(self):
        for kind in NEW:
            self.assertIn("W28", warnings(validate(with_term(kind, in_setup=False))), kind)
        self.assertNotIn("W28", warnings(validate(with_term("concept", in_setup=False))))

    def test_rule_63_still_refuses_unknown_kinds(self):
        r = validate(with_term("gene_product", in_setup=True))
        self.assertEqual(rules(r), ["63"])
        self.assertIn("biological_material", r.errors[0]["message"])

    def test_a_v038_file_keeps_its_kinds(self):
        d = with_term("substance", in_setup=True)
        d["schema_version"] = "0.38"
        self.assertIn("63", rules(validate(d)))


class TestLab(unittest.TestCase):
    def test_terms_may_have_the_new_kinds(self):
        from test_lab import LabCase
        self.assertTrue(set(NEW) <= core.ENUM["term_kind"])
        case = LabCase("run")
        case.setUp()
        try:
            for kind in NEW:
                case.add("term", {"term": kind, "kind": kind, "definition": "d"})
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
