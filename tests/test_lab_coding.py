"""Codes and codings (lab record spec v0.17 §5.19, §5.21; rules L25, L27; codings read by runs; export)."""

import json
import os
import sys
import unittest
from pathlib import Path

from claimset.lab import sources
from claimset.lab.core import LabError, verify
from claimset.lab.export import export
from claimset.lab.run import run_command

from test_lab import LabCase, TestCli
from test_lab_sources import PAGE

COUNT = """
import json, os
data = json.load(open(os.environ["CLAIMSET_CODINGS"], encoding="utf-8"))
code = {c["term"]: c["id"] for c in data["codes"]}["arrange for ensemble"]
threads = {c["source"] for c in data["codings"] if code in c["codes"]}
json.dump([{"quantity": "threads coded arrange for ensemble", "value": len(threads)}],
          open(os.environ["CLAIMSET_RESULTS"], "w"))
"""


class CodingCase(LabCase):
    def setUp(self):
        super().setUp()
        self.page = Path(self.tmp.name) / "thread.html"
        self.page.write_text(PAGE, encoding="utf-8")
        self.sr = sources.capture(self.lab, self.agent, file=str(self.page),
                                  url="https://musescore.org/en/node/42911", venue="musescore.org",
                                  published_at="2014-12-01")
        self.theme = self.add("term", {"term": "arranging", "kind": "concept",
                                       "definition": "Requests about arranging music.",
                                       "code": {"include": "any request about arranging"}})
        self.code = self.add("term", {"term": "arrange for ensemble", "kind": "concept",
                                      "definition": "A request that the software arrange existing music for a "
                                                    "given set of instruments.",
                                      "code": {"include": "asks for automatic arranging for named instruments",
                                               "exclude": "manual tools such as explode/implode",
                                               "examples": ["arrange this piano score for brass quintet"]},
                                      "broader": [self.theme]})

    def coding(self, words="arrange a piano score for a woodwind quintet", **extra):
        body = {"source": self.sr["id"], "codes": [self.code], "code_versions": {self.code: self.code},
                "unit": "post", "pass": 1}
        if words:
            sel = sources.quote(self.lab, self.sr, words)
            body["quote"] = {k: sel[k] for k in ("exact", "prefix", "suffix", "start", "end")}
        body.update(extra)
        return self.add("coding", {k: v for k, v in body.items() if v is not None})


class TestCodes(CodingCase):
    def test_a_code_is_a_concept_with_inclusion_criteria(self):
        for bad in ({"kind": "variable", "code": {"include": "x"}}, {"code": {}}, {"code": {"include": "x", "rule": 1}},
                    {"code": {"include": "x", "examples": "one"}}, {"broader": ["TM99"]}):
            body = dict({"term": "t", "kind": "concept", "definition": "d"}, **bad)
            with self.assertRaises(LabError, msg=bad):
                self.add("term", body)


class TestCodings(CodingCase):
    def test_a_passage_coded_with_a_code_of_the_codebook(self):
        cd = self.coding()
        self.coding(words=None, quote=None, unit="thread")                       # the whole source
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])
        self.assertEqual(self.lab.state().get(cd)["body"]["quote"]["start"],
                         sources.text_of(self.lab, self.sr).index("arrange a piano"))

    def test_L27_codes_are_terms_with_a_code_part(self):
        plain = self.add("term", {"term": "MuseScore", "kind": "method", "definition": "the notation program"})
        for codes in ([plain], ["TM99"], []):
            with self.assertRaises(LabError) as cm:
                self.coding(codes=codes, code_versions=None)
            self.assertIn(cm.exception.rule, ("L27", "L12", "schema"), codes)
        with self.assertRaises(LabError) as cm:
            self.coding(codes=[plain], code_versions=None)
        self.assertEqual(cm.exception.rule, "L27")

    def test_L25_a_quote_that_is_not_in_the_source_breaks_verify(self):
        cd = self.coding()
        body = dict(self.lab.state().get(cd)["body"])
        body["quote"] = dict(body["quote"], exact="arrange a piano score for a string quartet")
        self.add("coding", body, supersedes=cd)
        errors = verify(self.lab)["errors"]
        self.assertEqual([e["rule"] for e in errors], ["L25"])

    def test_recoding_and_withdrawing_are_corrections(self):
        cd = self.coding()
        other = self.add("term", {"term": "range checking", "kind": "concept", "definition": "d",
                                  "code": {"include": "asks to flag notes out of an instrument's range"}})
        cd2 = self.coding(codes=[other], code_versions={other: other})
        st = self.lab.state()
        self.assertEqual(len(sources.current_codings(st)), 2)
        self.add("coding", {"source": self.sr["id"], "codes": [], "withdrawn": True}, supersedes=cd2)
        self.assertEqual([c["id"] for c in sources.current_codings(self.lab.state())], [cd])
        with self.assertRaises(LabError):
            self.add("coding", {"source": self.sr["id"], "codes": []})                # no codes, not a withdrawal


class TestRunsReadCodings(CodingCase):
    def test_a_run_counts_from_the_codings_in_force(self):
        self.coding()
        script = Path(self.tmp.name) / "count.py"
        script.write_text(COUNT)
        out = run_command(self.lab, self.agent, [sys.executable, str(script)], echo=False, cwd=self.tmp.name)
        run = out["run"]["body"]
        self.assertEqual((run["exit_status"], run["codings"]["count"], run["codings"]["path"]),
                         ("success", 1, "codings.json"))
        self.assertIn({"path": "codings.json", "hash": run["codings"]["hash"]}, run["outputs"])
        self.assertEqual(out["results"][0]["body"]["value"], 1)
        snap = json.loads((self.lab.root / "runs" / out["run"]["id"] / "codings.json").read_text())
        self.assertEqual(snap["sources"][0]["venue"], "musescore.org")
        self.assertEqual(snap["codes"][0]["broader"], [self.theme])
        self.assertEqual(snap["codings"][0]["quote"]["exact"], "arrange a piano score for a woodwind quintet")
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])

    def test_no_codings_no_file(self):
        script = Path(self.tmp.name) / "noop.py"
        script.write_text("pass\n")
        out = run_command(self.lab, self.agent, [sys.executable, str(script)], echo=False, cwd=self.tmp.name)
        self.assertNotIn("codings", out["run"]["body"])


class TestExport(CodingCase):
    def test_codes_export_with_their_criteria_and_broader_terms(self):
        self.coding()
        self.add("status", {"target": self.hyp(), "to": "abandoned", "rationale": "r"})
        out = export(self.lab)
        defs = {d["term"]: d for d in out["draft"]["definitions"]}
        code = defs["arrange for ensemble"]
        self.assertIn("Coded when: asks for automatic arranging for named instruments.", code["definition"])
        self.assertIn("Not coded when: manual tools such as explode/implode.", code["definition"])
        self.assertEqual(code["broader"], [defs["arranging"]["id"]])
        self.assertIn("1 codings of captured sources", " | ".join(t["what"] for t in out["completion"]))


class TestCodeCli(TestCli):
    def test_term_code_and_coding(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        page = Path(self.tmp.name) / "thread.html"
        page.write_text(PAGE, encoding="utf-8")
        self.assertEqual(self.run_cli("lab", "capture", str(page), "--uri", "https://musescore.org/en/node/1")[0], 0)
        code, out, err = self.run_cli("lab", "term", "arrange for ensemble", "--kind", "concept", "--definition",
                                      "A request to arrange for given instruments.", "--include",
                                      "asks for automatic arranging", "--exclude", "manual tools")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("lab", "code", "SR1", "--codes", "TM1", "--excerpt", "I need this too",
                                      "--unit", "post", "--pass", "1")
        self.assertEqual(code, 0, err)
        events = [json.loads(l) for l in (Path(self.env["CLAIMSET_LAB"]) / "lab.jsonl").read_text().splitlines()]
        cd = events[-1]
        self.assertEqual((cd["id"], cd["body"]["codes"], cd["body"]["quote"]["exact"], cd["body"]["code_versions"]),
                         ("CD1", ["TM1"], "I need this too", {"TM1": "TM1"}))
        code, _, err = self.run_cli("lab", "code", "SR1", "--codes", "TM1", "--excerpt", "not in the page")
        self.assertNotEqual(code, 0)
        code, _, err = self.run_cli("lab", "code", "SR1", "--withdraw", "--supersedes", "CD1")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
