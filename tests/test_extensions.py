"""Extensions (spec §2.7): rule 80, warning 33, the linguistics extension (spec/extensions/linguistics-v0.1.md),
the move of glosses from v0.37 and the reading view."""

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

BASE = json.loads((ROOT / "tests" / "compat" / "0.38" / "submission-linguistics.json").read_text(encoding="utf-8"))


def doc():
    return copy.deepcopy(BASE)


def example(d):
    """The glossed example result R9 of E1."""
    return next(r for r in d["evidence"][0]["results"] if r["id"] == "R9")


def rules(report):
    return sorted({e["rule"] for e in report.errors})


def warnings(report):
    return sorted({w["rule"] for w in report.warnings})


class TestRule80(unittest.TestCase):
    def test_a_file_with_an_extension_is_valid(self):
        r = validate(doc())
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])

    def test_a_file_without_extensions_is_valid(self):
        d = doc()
        del d["extensions"]
        del example(d)["ext"]
        self.assertEqual(validate(d).errors, [])

    def test_an_unknown_extension_is_refused_and_the_core_still_checked(self):
        d = doc()
        d["extensions"]["synthesis"] = "0.1"
        d["claims"][0]["ext"] = {"synthesis": {"structure": "c1ccccc1"}}
        del d["claims"][1]["support"]                           # a core error
        r = validate(d)
        self.assertIn("80", rules(r))
        self.assertIn("2", rules(r))
        msg = next(e["message"] for e in r.errors if e["rule"] == "80")
        self.assertIn("does not know the extension synthesis", msg)
        self.assertIn("current validator", msg)

    def test_a_later_version_of_an_extension_is_refused(self):
        d = doc()
        d["extensions"]["linguistics"] = "0.9"
        r = validate(d)
        self.assertEqual(rules(r), ["80"])
        self.assertIn("knows 0.1", r.errors[0]["message"])

    def test_bad_declarations(self):
        for bad in ([], {"Linguistics": "0.1"}, {"linguistics": 0.1}, {"linguistics": "v0.1"}):
            d = doc()
            d["extensions"] = bad
            self.assertIn("80", rules(validate(d)), bad)

    def test_an_undeclared_block_is_refused(self):
        d = doc()
        del d["extensions"]
        self.assertEqual(rules(validate(d)), ["80"])

    def test_a_block_on_an_object_the_extension_does_not_allow(self):
        d = doc()
        d["claims"][0]["ext"] = {"linguistics": {"gloss": "x"}}
        r = validate(d)
        self.assertEqual(rules(r), ["80"])
        self.assertIn("allows result", r.errors[0]["message"])

    def test_ext_where_no_object_may_carry_it(self):
        d = doc()
        d["claims"][0]["support"][0]["ext"] = {}
        self.assertEqual(rules(validate(d)), ["80"])
        d = doc()
        d["ext"] = {}
        self.assertEqual(rules(validate(d)), ["80"])

    def test_blocks_must_be_objects(self):
        d = doc()
        example(d)["ext"] = ["linguistics"]
        self.assertIn("80", rules(validate(d)))
        d = doc()
        example(d)["ext"]["linguistics"] = "1SG-FUT-2SG.OBJ-love"
        self.assertIn("80", rules(validate(d)))

    def test_warning_33_a_declared_extension_unused(self):
        d = doc()
        del example(d)["ext"]
        r = validate(d)
        self.assertEqual(r.errors, [])
        self.assertEqual(warnings(r), ["W33"])


class TestLinguistics(unittest.TestCase):
    def test_rule_1_only_on_excerpts(self):
        d = doc()
        d["evidence"][0]["results"].append({"id": "R10", "variable": "number of speakers", "value": 1200,
                                            "ext": {"linguistics": {"gloss": "x"}}})
        self.assertEqual(rules(validate(d)), ["linguistics.1"])

    def test_rule_2_fields_and_gloss(self):
        d = doc()
        example(d)["ext"]["linguistics"]["tone"] = "high"
        self.assertEqual(rules(validate(d)), ["linguistics.2"])
        d = doc()
        del example(d)["ext"]["linguistics"]["gloss"]
        self.assertEqual(rules(validate(d)), ["linguistics.2"])
        d = doc()
        example(d)["ext"]["linguistics"]["segmented"] = ""
        self.assertIn("linguistics.2", rules(validate(d)))

    def test_rule_3_alignment(self):
        d = doc()
        example(d)["ext"]["linguistics"]["gloss"] = "1SG-FUT love"                 # two words against one
        self.assertEqual(rules(validate(d)), ["linguistics.3"])
        d = doc()
        example(d)["ext"]["linguistics"]["gloss"] = "1SG-FUT-love"                 # three morphemes against four
        self.assertEqual(rules(validate(d)), ["linguistics.3"])

    def test_rule_4_judgement(self):
        d = doc()
        example(d)["ext"]["linguistics"]["judgement"] = "*"
        self.assertEqual(validate(d).errors, [])
        example(d)["ext"]["linguistics"]["judgement"] = "bad"
        self.assertEqual(rules(validate(d)), ["linguistics.4"])

    def test_warning_1_unaligned_without_segmentation(self):
        d = doc()
        del example(d)["ext"]["linguistics"]["segmented"]
        example(d)["ext"]["linguistics"]["gloss"] = "1SG-FUT-2SG.OBJ-love you"
        r = validate(d)
        self.assertEqual(r.errors, [])
        self.assertEqual(warnings(r), ["linguistics.w1"])


class TestGlossMoved(unittest.TestCase):
    def v037(self):
        d = doc()
        d["schema_version"] = "0.37"
        del d["extensions"]
        r = example(d)
        r["value"]["gloss"] = r.pop("ext")["linguistics"]["gloss"]
        return d

    def test_a_v037_file_with_a_gloss_stays_valid(self):
        self.assertEqual(validate(self.v037()).errors, [])

    def test_a_gloss_in_the_excerpt_is_refused_from_v038(self):
        d = self.v037()
        d["schema_version"] = "0.38"
        r = validate(d)
        self.assertEqual(rules(r), ["66"])
        self.assertIn("ext.linguistics.gloss", r.errors[0]["message"])

    def test_migrate_moves_the_gloss(self):
        out, done, _ = migrate(self.v037())
        self.assertEqual(out["extensions"], {"linguistics": "0.1"})
        self.assertEqual(example(out)["ext"]["linguistics"]["gloss"], "1SG-FUT-2SG.OBJ-love")
        self.assertNotIn("gloss", example(out)["value"])
        self.assertIn("E1.R9: gloss -> ext.linguistics.gloss", done)
        self.assertEqual(validate(out).errors, [])


class TestReadingView(unittest.TestCase):
    def test_blocks_are_shown_as_data(self):
        text = render(doc())
        self.assertIn("extensions linguistics 0.1", text)
        self.assertIn("[linguistics: segmented ni-ta-ku-penda; gloss 1SG-FUT-2SG.OBJ-love]", text)


if __name__ == "__main__":
    unittest.main()
