"""Counts of coded material and the tests that use them (lab record spec v0.18 §5.21.1, §5.7.5; §5.20 authors;
§7.1 desk mode; pinned lab-record links on several hosts)."""

import json
import os
import sys
import unittest
from pathlib import Path

from claimset.extensions import qualitative_count as counter
from claimset.lab import qualitative, sources
from claimset.lab.core import LabError, plan_flags, verify
from claimset.lab.export import _blob_pattern, export
from claimset.lab.run import count_run

from test_lab import LabCase, TestCli

THREADS = [  # (handle, published, text): five threads asking for arranging, from four authors and four years
    ("ann", "2014-03-02", "Could MuseScore arrange this piano piece for brass quintet automatically?"),
    ("bob", "2016-07-19", "Please add a way to arrange a score for string quartet."),
    ("ann", "2016-11-30", "Again: arrange for woodwind ensemble would save me hours."),
    ("cy", "2019-01-05", "Arrange for choir from a piano score, like some other programs do."),
    ("dee", "2021-06-12", "An arrange-for-band feature is the thing I miss most."),
    ("eve", "2022-02-01", "Please check that notes stay within each instrument's range."),
]


class CountCase(LabCase):
    def setUp(self):
        super().setUp()
        self.theme = self.add("term", {"term": "arranging", "kind": "concept", "definition": "Requests about arranging.",
                                       "code": {"include": "any request about arranging"}})
        self.arr = self.add("term", {"term": "arrange for ensemble", "kind": "concept",
                                     "definition": "A request that the program arrange music for given instruments.",
                                     "code": {"include": "asks for automatic arranging for named instruments"},
                                     "broader": [self.theme]})
        self.rng = self.add("term", {"term": "range checking", "kind": "concept", "definition": "d",
                                     "code": {"include": "asks to flag notes outside an instrument's range"}})
        self.src = []
        for i, (who, when, text) in enumerate(THREADS):
            f = Path(self.tmp.name) / f"t{i}.txt"
            f.write_text(text + "\n", encoding="utf-8")
            self.src.append(sources.capture(self.lab, self.agent, file=str(f), url=f"https://musescore.org/node/{i}",
                                            venue="musescore.org", published_at=when, author=who))

    def code_all(self):
        for i, s in enumerate(self.src):
            code = self.rng if i == 5 else self.arr
            self.add("coding", {"source": s["id"], "codes": [code], "code_versions": {code: code}, "unit": "thread"})

    def plan(self, hyps=None):
        h = hyps or [self.hyp("Forum threads ask for automatic arranging in many different years.")]
        return self.add("plan", {
            "kind": "confirmatory", "hypotheses": h, "design": "all threads of the search, coded", "analysis": "count",
            "decision": {"framework": "qualitative",
                         "quantities": {"years": {"count": {"codes": [self.theme], "unit": "source",
                                                            "distinct_by": "year"}},
                                        "authors": {"count": {"codes": [self.arr], "distinct_by": "author"}}},
                         "supported": "years >= 4 and authors >= 4", "not_supported": "years < 2"}})


class TestCounter(CountCase):
    def snapshot(self):
        return qualitative.codings_snapshot(self.lab.state())

    def test_counts_follow_broader_codes_and_distinct_keys(self):
        self.code_all()
        snap = self.snapshot()
        c = lambda **spec: counter.count(snap, spec)["value"]
        self.assertEqual(c(codes=[self.arr]), 5)                                  # sources
        self.assertEqual(c(codes=[self.theme]), 5)                                # narrower codes count for the theme
        self.assertEqual(c(codes=[self.theme], distinct_by="year"), 4)            # 2014, 2016, 2019, 2021
        self.assertEqual(c(codes=[self.arr], distinct_by="author"), 4)            # ann twice
        self.assertEqual(c(codes=[self.arr, self.rng], match="all"), 0)
        self.assertEqual(c(codes=[self.arr], where={"published_from": "2016", "published_to": "2019"}), 3)
        self.assertEqual(c(codes=[self.arr], unit="coding"), 5)

    def test_spec_problems(self):
        self.assertEqual(counter.spec_problems("n", {"codes": ["TM1"], "distinct_by": "year"}), [])
        for bad in ({}, {"codes": []}, {"codes": ["TM1"], "unit": "post"}, {"codes": ["TM1"], "distinct_by": "x"},
                    {"codes": ["TM1"], "where": {"author": "a"}}, {"codes": ["TM1"], "per": 1}):
            self.assertTrue(counter.spec_problems("n", bad), bad)

    def test_authors_are_pseudonyms(self):
        a = [s["body"]["author"] for s in self.src]
        self.assertTrue(all(x.startswith("a-") and len(x) == 14 for x in a))
        self.assertEqual(a[0], a[2])                                              # the same handle, the same pseudonym
        self.assertNotEqual(a[0], a[1])
        self.assertNotIn("ann", json.dumps([s["body"] for s in self.src]))
        f = Path(self.tmp.name) / "org.txt"
        f.write_text("x\n", encoding="utf-8")
        org = sources.capture(self.lab, self.agent, file=str(f), author="MuseScore BVBA", author_as_given=True)
        self.assertEqual(org["body"]["author"], "MuseScore BVBA")


class TestCountedTests(CountCase):
    def test_a_plan_decided_by_counts(self):
        self.code_all()
        pl = self.plan()
        out = count_run(self.lab, self.agent, plan=pl)
        vals = {r["body"]["variable"]: r["body"]["value"] for r in out["results"]}
        self.assertEqual((vals["count: years"], vals["count: authors"]), (4, 4))
        self.assertEqual(out["run"]["body"]["label"], "confirmatory")
        st = self.lab.state()
        h = st.get(pl)["body"]["hypotheses"][0]
        self.add("status", {"target": h, "to": "supported", "rationale": "counted", "plan": pl,
                            "evidence": [r["id"] for r in out["results"]]})
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])
        flags = plan_flags(st, st.plan_rule_version(pl), [out["run"]])
        self.assertIn("coded_before_plan", flags)                 # coded, then planned: the flag says so
        claim = next(c for c in export(self.lab)["draft"]["claims"] if c.get("hypothesis", {}).get("lab_event") == h)
        self.assertEqual(claim["test"]["quantities"]["years"], {"variable": "count: years"})
        self.assertEqual(claim["ext"]["qualitative"]["counts"]["years"]["distinct_by"], "year")
        self.assertIn("coded_before_plan", claim["ext"]["qualitative"]["test_flags"])   # the extension's flag
        self.assertNotIn("code_changed_after_plan", claim["test"].get("flags", []))  # the counter is the tool's code
        self.assertEqual(export(self.lab)["draft"]["extensions"], {"qualitative": "0.1"})
        draft = export(self.lab)["draft"]
        defs = {d["term"]: d["id"] for d in draft["definitions"]}
        self.assertEqual(claim["ext"]["qualitative"]["counts"]["years"]["codes"], [defs["arranging"]])
        ev = next(e for e in draft["evidence"] if e["id"] == claim["support"][0]["evidence"])
        self.assertEqual((claim["support"][0]["kind"], ev["profile"], ev["analytic_approach"], ev["data_sources"],
                          ev["n_participants_or_cases"]), ("empirical_qual", "qual", "content_analysis", ["document"], 6))
        mapping = export(self.lab)["mapping"]
        self.assertEqual(sorted(mapping[a] for a in ev["artefacts"]), sorted(s["id"] for s in self.src))          # every source the run read
        from claimset.validate import validate
        rules = {e["rule"] for e in validate(draft).errors}
        self.assertFalse(rules & {"10", "47", "66", "78"}, rules)

    def test_coded_after_the_plan_is_not_flagged_and_a_changed_code_is(self):
        pl = self.plan()
        self.code_all()
        self.add("term", {"term": "arrange for ensemble", "kind": "concept", "definition": "widened",
                          "code": {"include": "any wish for arranging help"}, "broader": [self.theme]},
                 supersedes=self.arr)
        out = count_run(self.lab, self.agent, plan=pl)
        st = self.lab.state()
        flags = plan_flags(st, st.plan_rule_version(pl), [out["run"]])
        self.assertNotIn("coded_before_plan", flags)
        self.assertIn("codebook_changed_after_plan", flags)

    def test_L27_a_count_names_codes(self):
        plain = self.add("term", {"term": "MuseScore", "kind": "method", "definition": "the program"})
        with self.assertRaises(LabError) as cm:
            self.add("plan", {"kind": "confirmatory", "hypotheses": [self.hyp()], "design": "d", "analysis": "a",
                              "decision": {"framework": "qualitative",
                                           "quantities": {"n": {"count": {"codes": [plain]}}},
                                           "supported": "n >= 5", "not_supported": "n < 2"}})
        self.assertEqual(cm.exception.rule, "qualitative.L3")

    def test_L15_a_broken_count_is_refused(self):
        with self.assertRaises(LabError) as cm:
            self.add("plan", {"kind": "confirmatory", "hypotheses": [self.hyp()], "design": "d", "analysis": "a",
                              "decision": {"framework": "qualitative",
                                           "quantities": {"n": {"count": {"codes": [self.arr], "unit": "post"}}},
                                           "supported": "n >= 5", "not_supported": "n < 2"}})
        self.assertEqual(cm.exception.rule, "qualitative.L3")

    def test_an_exploratory_count(self):
        self.code_all()
        out = count_run(self.lab, self.agent, counts={"n": {"codes": [self.arr], "distinct_by": "year"}})
        self.assertEqual(out["run"]["body"]["label"], "exploratory")
        self.assertEqual(out["results"][0]["body"]["value"], 4)


class TestHosts(unittest.TestCase):
    def test_pinned_link_patterns(self):
        cases = {"https://github.com/o/r": "https://github.com/o/r/blob/C/lab/lab.jsonl",
                 "https://gitlab.com/o/r.git": "https://gitlab.com/o/r/-/blob/C/lab/lab.jsonl",
                 "https://codeberg.org/o/r": "https://codeberg.org/o/r/src/commit/C/lab/lab.jsonl",
                 "https://bitbucket.org/o/r": "https://bitbucket.org/o/r/src/C/lab/lab.jsonl",
                 "https://gitlab.uni.ac.uk/o/r": "https://gitlab.uni.ac.uk/o/r/-/blob/C/lab/lab.jsonl"}
        for repo, want in cases.items():
            self.assertEqual(_blob_pattern(repo).format(repo=repo.removesuffix(".git"), commit="C",
                                                        path="lab/lab.jsonl"), want)
        self.assertIsNone(_blob_pattern("https://example.org/o/r"))
        self.assertIsNone(_blob_pattern(None))


class TestCountCli(TestCli):
    def test_count_from_the_command_line(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        page = Path(self.tmp.name) / "t.txt"
        page.write_text("Please arrange for brass quintet.\n", encoding="utf-8")
        self.run_cli("lab", "capture", str(page), "--uri", "https://x.org/1", "--published-at", "2015-01-01",
                     "--author", "ann")
        self.run_cli("lab", "term", "arrange for ensemble", "--kind", "concept", "--definition", "d",
                     "--include", "asks for arranging")
        self.run_cli("lab", "code", "SR1", "--codes", "TM1")
        code, out, err = self.run_cli("lab", "count", "--codes", "TM1", "--distinct-by", "year")
        self.assertEqual(code, 0, err)
        self.assertIn("count: n = 1", out)
        self.assertIn("RN1 (exploratory, success)", err)
        self.run_cli("lab", "question", "Do people ask for arranging?")
        self.run_cli("lab", "hypothesis", "People ask for arranging.", "--question", "Q1")
        code, out, err = self.run_cli("lab", "plan", "--kind", "confirmatory", "--hypotheses", "H1", "--design", "d",
                                      "--analysis", "a", "--framework", "qualitative",
                                      "--quantity", 'n={"count": {"codes": ["TM1"]}}',
                                      "--supported", "n >= 1", "--not-supported", "n < 1")
        self.assertEqual(code, 0, err)
        from claimset.lab.core import Lab
        lab = Lab(Path(os.environ["CLAIMSET_LAB"]) if os.environ.get("CLAIMSET_LAB") else Path("lab"))
        plan = lab.state().get("PL1")["body"]
        self.assertEqual(plan["decision"]["quantities"], {"n": {"variable": "count: n"}})
        self.assertEqual(plan["ext"], {"qualitative": {"counts": {"n": {"codes": ["TM1"]}}}})
        code, out, err = self.run_cli("lab", "count", "--plan", "PL1")
        self.assertEqual(code, 0, err)
        self.assertIn("count: n = 1", out)


if __name__ == "__main__":
    unittest.main()


class TestTrialFixes(CountCase):
    """What a first qualitative study needed: sources read and given no code, archives found later, excerpts and
    source facts in the export, a limitation for single coding, a captured copy standing in for an archive."""

    def test_none_apply_keeps_the_denominator(self):
        self.code_all()
        f = Path(self.tmp.name) / "other.txt"
        f.write_text("A thread about playback speed.\n", encoding="utf-8")
        other = sources.capture(self.lab, self.agent, file=str(f), url="https://musescore.org/node/99",
                                published_at="2020-05-05", author="fay")
        self.add("coding", {"source": other["id"], "codes": [], "none_apply": True, "unit": "thread"})
        with self.assertRaises(LabError):
            self.add("coding", {"source": other["id"], "codes": [self.arr], "none_apply": True})
        snap = qualitative.codings_snapshot(self.lab.state())
        self.assertEqual(len({c["source"] for c in snap["codings"]}), 7)
        self.assertEqual(counter.count(snap, {"codes": [self.arr]})["value"], 5)    # not counted for any code

    def test_a_source_is_corrected_with_the_same_files(self):
        old = self.src[0]
        new = sources.correct(self.lab, self.agent, old["id"], archive_uri="https://web.archive.org/web/2026/x")
        self.assertEqual(new["supersedes"], old["id"])
        self.assertEqual(new["body"]["files"], old["body"]["files"])
        self.assertEqual(new["body"]["archive_uri"], "https://web.archive.org/web/2026/x")
        self.assertTrue((self.lab.root / "private" / "sources" / new["id"]).is_dir())
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])

    def test_export_of_coded_evidence(self):
        self.code_all()
        # quote one passage
        sel = sources.quote(self.lab, self.src[1], "arrange a score for string quartet")
        self.add("coding", {"source": self.src[1]["id"], "codes": [self.arr], "code_versions": {self.arr: self.arr},
                            "quote": {k: sel[k] for k in ("exact", "prefix", "suffix", "start", "end")}})
        pl = self.plan()
        out = count_run(self.lab, self.agent, plan=pl)
        h = self.lab.state().get(pl)["body"]["hypotheses"][0]
        self.add("status", {"target": h, "to": "supported", "rationale": "counted", "plan": pl,
                            "evidence": [r["id"] for r in out["results"]]})
        ex = export(self.lab)
        draft, mapping = ex["draft"], ex["mapping"]
        claim = next(c for c in draft["claims"] if c.get("hypothesis", {}).get("lab_event") == h)
        ev = next(e for e in draft["evidence"] if e["id"] == claim["support"][0]["evidence"])
        excerpts = [r for r in ev["results"] if isinstance(r.get("value"), dict)]
        self.assertEqual(len(excerpts), 1)
        loc = excerpts[0]["value"]["locator"]
        self.assertEqual((mapping[loc["artefact"]], excerpts[0]["value"]["text"]),
                         (self.src[1]["id"], "arrange a score for string quartet"))
        counts = [r for r in ev["results"] if r["variable"].startswith("count:")]
        self.assertTrue(all(r["conditions"]["code"].startswith("D") for r in counts if "code" in r["conditions"]))
        self.assertIn("Counted by the counter of the lab record tool", ev["execution"]["notes"])
        lim = next(l for l in draft["limitations"] if "one coder in one pass" in l["statement"])
        self.assertIn(claim["id"], lim["affects"])
        art = next(a for a in draft["artefacts"] if mapping.get(a["id"]) == self.src[0]["id"])
        self.assertIn("published: 2014-03-02", art["description"])
        self.assertIn("venue: musescore.org", art["description"])
        from claimset.validate import validate
        rep = validate(draft)
        rules = {e["rule"] for e in rep.errors}
        self.assertNotIn("51", rules)                               # captured copies stand in for archives
        self.assertIn("W32", {w["rule"] for w in rep.warnings})
        self.assertFalse(rules & {"10", "47", "66", "78"}, rep.errors)

    def test_list_lines(self):
        from claimset.lab.summary import list_line
        self.code_all()
        lines = [list_line(e) for e in self.lab.read()]
        self.assertTrue(any("arrange for ensemble [concept]; coded when:" in l for l in lines))
        self.assertTrue(any("musescore.org · 2014-03-02 · private" in l for l in lines))
        self.assertTrue(any(f"{self.src[0]['id']} {self.arr}: (whole source)" in l for l in lines))
