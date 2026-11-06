"""Lab record v0.20: the claimset specification is the record's vocabulary (§2.1), extensions (§5.22, rule L30),
results that name their `variable`, and records begun before v0.20, read in the forms written then."""

import json
import sys
import unittest
from pathlib import Path

from claimset import validate as claimset
from claimset.lab import core, qualitative
from claimset.lab.core import Lab, LabError, verify
from claimset.lab.export import export
from claimset.lab.run import run_command

from test_lab import LabCase, ticking_clock

MODEL = [{"provider": "P", "name": "M", "version": "1"}]
RESULT = """
import json, os
json.dump([{"variable": "share of fits that failed", "value": 0.2}], open(os.environ["CLAIMSET_RESULTS"], "w"))
"""
OLD_RESULT = RESULT.replace('"variable"', '"quantity"')


class TestVocabulary(unittest.TestCase):
    """The lists the record shares with a claimset are the claimset's, not copies (§2.1)."""

    def test_shared_lists_are_the_claimsets(self):
        self.assertIs(core.ENUM["actor_kind"], claimset.ENUM["contributor_type"])
        self.assertIs(core.ENUM["artefact_kind"], claimset.ENUM["artefact_kind"])
        self.assertIs(core.ENUM["assumption_category"], claimset.ENUM["assumption_category"])
        self.assertIs(core.ENUM["withheld_reason"], claimset.ENUM["reason"])
        self.assertEqual(core.ENUM["term_kind"] - {"measure"}, claimset.ENUM["definition_kind"])
        self.assertIs(core.SETUP_ROLES, claimset.SETUP_ROLES)
        from claimset.lab import decision
        self.assertEqual(set(decision.FRAMEWORKS), claimset.ENUM["framework"])
        self.assertIs(decision.REQUIRED, claimset.TEST_PARAMETERS)
        self.assertEqual(set(core.FLAGS), claimset.ENUM["test_flag"])

    def test_a_kind_the_claimset_adds_is_accepted_at_once(self):
        try:
            claimset.ENUM["definition_kind"].add("substance")
            core.ENUM["term_kind"] = claimset.ENUM["definition_kind"] | {"measure"}
            case = LabCase("run")
            case.setUp()
            try:
                case.add("term", {"term": "benzene", "kind": "substance", "definition": "C6H6"})
            finally:
                case.tearDown()
        finally:
            claimset.ENUM["definition_kind"].discard("substance")
            core.ENUM["term_kind"] = claimset.ENUM["definition_kind"] | {"measure"}


class TestExtensions(LabCase):
    def test_L30_blocks_need_a_declared_extension(self):
        body = {"term": "t", "kind": "concept", "definition": "d", "ext": {"qualitative": {"code": {"include": "x"}}}}
        with self.assertRaises(LabError) as cm:
            self.lab.append("term", body, by=self.agent)
        self.assertEqual(cm.exception.rule, "L30")
        self.lab.append("extension", {"name": "qualitative", "version": "0.1"}, by=self.agent)
        self.lab.append("term", body, by=self.agent)
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])

    def test_L30_extension_event_types_need_their_extension(self):
        with self.assertRaises(LabError) as cm:
            self.lab.append("coding", {"source": "SR1", "codes": ["TM1"]}, by=self.agent)
        self.assertEqual(cm.exception.rule, "L30")

    def test_L30_unknown_extensions_and_versions_are_refused(self):
        for body in ({"name": "synthesis", "version": "0.1"}, {"name": "qualitative", "version": "0.9"}):
            with self.assertRaises(LabError) as cm:
                self.lab.append("extension", body, by=self.agent)
            self.assertEqual(cm.exception.rule, "L30")
            self.assertIn("does not know", cm.exception.message)

    def test_L30_declared_once_and_on_allowed_events_only(self):
        self.lab.append("extension", {"name": "qualitative", "version": "0.1"}, by=self.agent)
        with self.assertRaises(LabError) as cm:
            self.lab.append("extension", {"name": "qualitative", "version": "0.1"}, by=self.agent)
        self.assertEqual(cm.exception.rule, "L30")
        with self.assertRaises(LabError) as cm:            # qualitative has no block on a question
            self.lab.append("question", {"text": "Q?", "ext": {"qualitative": {}}}, by=self.agent)
        self.assertEqual(cm.exception.rule, "L30")
        with self.assertRaises(LabError) as cm:            # linguistics is not declared
            self.lab.append("term", {"term": "t", "kind": "concept", "definition": "d",
                                     "ext": {"linguistics": {}}}, by=self.agent)
        self.assertEqual(cm.exception.rule, "L30")

    def test_qualitative_L4_block_fields(self):
        self.lab.append("extension", {"name": "qualitative", "version": "0.1"}, by=self.agent)
        with self.assertRaises(LabError) as cm:
            self.lab.append("term", {"term": "t", "kind": "concept", "definition": "d",
                                     "ext": {"qualitative": {"theme": "x"}}}, by=self.agent)
        self.assertEqual(cm.exception.rule, "qualitative.L4")

    def test_a_code_in_the_old_form_is_refused_in_a_new_record(self):
        with self.assertRaises(LabError) as cm:
            self.lab.append("term", {"term": "t", "kind": "concept", "definition": "d", "code": {"include": "x"}},
                            by=self.agent)
        self.assertIn("ext.qualitative.code", cm.exception.message)

    def test_results_name_their_variable(self):
        script = Path(self.tmp.name) / "r.py"
        script.write_text(RESULT, encoding="utf-8")
        out = run_command(self.lab, self.agent, [sys.executable, str(script)], echo=False)
        self.assertEqual(out["results"][0]["body"]["variable"], "share of fits that failed")
        self.assertNotIn("quantity", out["results"][0]["body"])
        with self.assertRaises(LabError):
            self.lab.append("result", {"run": out["run"]["id"], "quantity": "x", "value": 1,
                                       "source": "results.json"}, by=self.agent)

    def test_a_results_file_may_still_say_quantity(self):
        script = Path(self.tmp.name) / "r.py"
        script.write_text(OLD_RESULT, encoding="utf-8")
        out = run_command(self.lab, self.agent, [sys.executable, str(script)], echo=False)
        self.assertEqual(out["results"][0]["body"]["variable"], "share of fits that failed")


class TestRecordsBeforeV020(unittest.TestCase):
    """A record begun under v0.19 keeps its forms: `code` on terms, codings without a declaration, `codings` on
    runs, `quantity` in results and decision rules, count quantities; export writes the claimset forms."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = Lab(Path(self.tmp.name) / "old", clock=ticking_clock())
        self.lab.root.mkdir()
        (self.lab.root / "runs").mkdir()
        self.lab.append("init", {"lab_spec_version": "0.19", "working_title": "t"}, by=None)
        self.agent = self.lab.append("actor", {"kind": "agent", "name": "A", "models": MODEL}, by="AC1")["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_forms_are_read_and_written(self):
        st = self.lab.state()
        self.assertTrue(st.legacy)
        self.assertEqual(st.extensions, {"qualitative": "0.1"})
        code = self.lab.append("term", {"term": "arranging", "kind": "concept", "definition": "d",
                                        "code": {"include": "asks for arranging"}}, by=self.agent)["id"]
        self.assertEqual(qualitative.code_of(self.lab.state().get(code)["body"]), {"include": "asks for arranging"})
        script = Path(self.tmp.name) / "r.py"
        script.write_text(RESULT, encoding="utf-8")
        out = run_command(self.lab, self.agent, [sys.executable, str(script)], echo=False)
        self.assertEqual(out["results"][0]["body"]["quantity"], "share of fits that failed")   # as v0.19 wrote it
        body, needs = core.record_forms(self.lab.state(), "plan", {"decision": {"quantities": {
            "n": {"count": {"codes": [code]}}, "p": {"variable": "p-value"}}}})
        self.assertEqual(body["decision"]["quantities"], {"n": {"count": {"codes": [code]}}, "p": {"quantity": "p-value"}})
        self.assertNotIn("ext", body)
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])
        with self.assertRaises(LabError):          # the extension event is not needed, and is refused twice
            self.lab.append("extension", {"name": "qualitative", "version": "0.1"}, by=self.agent)

    def test_export_writes_the_claimset_forms(self):
        h_q = self.lab.append("question", {"text": "Q"}, by=self.agent)["id"]
        self.lab.append("term", {"term": "arranging", "kind": "concept", "definition": "Requests about arranging.",
                                 "code": {"include": "asks for arranging"}}, by=self.agent)
        h = self.lab.append("hypothesis", {"statement": "Some threads ask for arranging.", "question": h_q,
                                           "origin": "a_priori"}, by=self.agent)["id"]
        self.lab.append("status", {"target": h, "to": "abandoned", "rationale": "r"}, by=self.agent)
        draft = export(self.lab)["draft"]
        d = next(x for x in draft["definitions"] if x["term"] == "arranging")
        self.assertEqual(d["ext"], {"qualitative": {"code": {"include": "asks for arranging"}}})
        self.assertEqual(draft["extensions"], {"qualitative": "0.1"})


if __name__ == "__main__":
    unittest.main()
