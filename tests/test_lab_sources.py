"""Captured sources (lab record spec v0.16 §5.20; rules L24-L26; flag sources_captured_after_plan; export §9)."""

import io
import json
import os
import sys
import unittest
from pathlib import Path

from claimset.lab import sources
from claimset.lab.core import LabError, plan_flags, verify
from claimset.lab.export import export
from claimset.lab.run import run_command

from test_lab import LabCase, TestCli

PAGE = """<html><head><title>Arrange for</title><script>var x = 1;</script></head><body>
<h1>Feature request: an "Arrange for" command</h1>
<p>It would be great if MuseScore could   arrange a piano score
for a woodwind quintet automatically.</p><p>Second post: +1, I need this too.</p></body></html>"""

COUNT = """
import json, os, sys
text = open(sys.argv[1], encoding="utf-8").read()
json.dump([{"quantity": "threads asking for arranging", "value": text.count("arrange")}],
          open(os.environ["CLAIMSET_RESULTS"], "w"))
"""


class SourceCase(LabCase):
    def setUp(self):
        super().setUp()
        self.page = Path(self.tmp.name) / "thread.html"
        self.page.write_text(PAGE, encoding="utf-8")

    def capture(self, **kw):
        kw.setdefault("file", str(self.page))
        kw.setdefault("url", "https://musescore.org/en/node/42911")
        return sources.capture(self.lab, self.agent, **kw)


class TestCapture(SourceCase):
    def test_a_saved_page_is_recorded_with_its_hash_and_text(self):
        e = capture = self.capture(retrieved_at="2026-11-05T11:00:00Z", title="Arrange for", venue="musescore.org")
        b = capture["body"]
        self.assertEqual((e["id"], b["method"], b["format"], b["visibility"], b["rights"]),
                         ("SR1", "saved", "text/html", "private", {"holder": "third_party"}))
        folder = self.lab.root / "private" / "sources" / "SR1"       # private: kept out of git (v0.19)
        self.assertEqual(sources.source_dir(self.lab, e), folder)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["text.txt", "thread.html"])
        text = (folder / "text.txt").read_text()
        self.assertIn("arrange a piano score for a woodwind quintet automatically.", text)
        self.assertNotIn("var x", text)
        self.assertTrue(verify(self.lab)["ok"], verify(self.lab)["errors"])

    def test_a_changed_or_added_file_breaks_L24(self):
        folder = sources.source_dir(self.lab, self.capture())
        (folder / "thread.html").write_text(PAGE + "edited", encoding="utf-8")
        (folder / "extra.txt").write_text("x", encoding="utf-8")
        errors = verify(self.lab)["errors"]
        self.assertEqual([e["rule"] for e in errors], ["L24", "L24"])
        self.assertIn("changed after it was captured", errors[0]["message"])
        self.assertTrue(verify(self.lab, check_files=False)["ok"])

    def test_others_content_is_published_only_under_its_own_terms(self):
        with self.assertRaises(LabError) as cm:
            self.capture(visibility="public")
        self.assertEqual(cm.exception.rule, "L26")
        for root in (self.lab.root / "sources", self.lab.root / "private" / "sources"):
            self.assertFalse(root.exists() and any(p.name.startswith(".pending") for p in root.iterdir()))
        self.capture(visibility="public", terms="GitHub Terms of Service, section D.5")
        self.capture(holder="study", licence="CC-BY-4.0")              # the study's own material: public by default
        self.assertEqual(self.lab.read()[-1]["body"]["visibility"], "public")

    def test_bad_bodies_are_refused(self):
        good = self.capture()["body"]
        for change in ({"method": "copied"}, {"visibility": "secret"}, {"rights": {}},
                       {"files": [{"path": "a/b.html", "hash": good["files"][0]["hash"], "role": "original"}]},
                       {"retrieved_at": "yesterday"}, {"method": "fetched", "uri": None}):
            body = {k: v for k, v in dict(good, **change).items() if v is not None}
            with self.assertRaises(LabError, msg=change):
                self.lab.append("source", body, by=self.agent)

    def test_fetch_records_the_method_and_time(self):
        class Resp(io.BytesIO):
            class headers:
                @staticmethod
                def get_content_type():
                    return "text/html"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        old = sources._open
        sources._open = lambda url, timeout: Resp(PAGE.encode())
        try:
            e = sources.capture(self.lab, self.agent, url="https://musescore.org/en/node/42911", fetch_url=True)
        finally:
            sources._open = old
        self.assertEqual((e["body"]["method"], e["body"]["format"]), ("fetched", "text/html"))
        self.assertTrue(e["body"]["retrieved_at"].endswith("Z"))

    def test_quotes_are_checked_against_the_text(self):
        src = self.capture()
        sel = sources.quote(self.lab, src, "arrange a piano score\n for a woodwind quintet")
        self.assertEqual(sel["exact"].split(), "arrange a piano score for a woodwind quintet".split())
        self.assertEqual((sel["source"], sel["occurrences"]), ("SR1", 1))
        self.assertTrue(sel["prefix"].endswith("could "))
        with self.assertRaises(LabError) as cm:
            sources.quote(self.lab, src, "arrange a piano score for a string quartet")
        self.assertEqual(cm.exception.rule, "L25")


class TestPrivateSources(SourceCase):
    """Lab record v0.19 §5.20: a private source's files and the pseudonym salt are kept out of git, in
    lab/private/; the record holds their hashes, and a checkout without them says what it could not check."""

    def other_checkout(self):
        """The lab as another checkout of the branch has it: everything but lab/private/."""
        import shutil
        from claimset.lab.core import Lab
        dest = Path(self.tmp.name) / "clone" / "lab"
        shutil.copytree(self.lab.root, dest, ignore=lambda d, names: ["private"] if Path(d) == self.lab.root else [])
        return Lab(dest)

    def test_private_files_and_the_salt_are_kept_out_of_git(self):
        priv = self.capture(author="jojo")
        pub = self.capture(holder="study", licence="CC-BY-4.0")
        self.assertEqual(sources.source_dir(self.lab, priv), self.lab.root / "private" / "sources" / "SR1")
        self.assertEqual(sources.source_dir(self.lab, pub), self.lab.root / "sources" / "SR2")
        self.assertEqual((self.lab.root / "private" / ".gitignore").read_text().splitlines()[-1], "*")
        self.assertTrue((self.lab.root / "private" / "pseudonym-salt").is_file())
        self.assertFalse((self.lab.root / "pseudonym-salt").exists())
        self.assertFalse((self.lab.root / "sources" / "SR1").exists())
        rep = verify(self.lab)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertFalse([n for n in rep["notes"] if "private" in n or "salt" in n], rep["notes"])

    def test_a_checkout_without_the_private_files_verifies_what_it_has(self):
        src = self.capture(author="jojo")
        sel = sources.quote(self.lab, src, "I need this too")
        code = self.add("term", {"term": "support", "kind": "concept", "definition": "d", "code": {"include": "+1"}})
        self.add("coding", {"source": src["id"], "codes": [code], "code_versions": {code: code},
                            "quote": {k: sel[k] for k in ("exact", "prefix", "suffix", "start", "end")}})
        other = self.other_checkout()
        rep = verify(other)
        self.assertTrue(rep["ok"], rep["errors"])                     # nothing there to break L24 or L25
        note = " | ".join(rep["notes"])
        self.assertIn("private sources SR1 are not in this checkout", note)
        self.assertIn("no pseudonym salt", note)
        with self.assertRaises(LabError) as cm:
            sources.quote(other, src, "I need this too")
        self.assertIn("copy lab/private/", cm.exception.message)
        with self.assertRaises(LabError) as cm:                         # pseudonyms there would not match
            sources.capture(other, self.agent, file=str(self.page), author="jojo")
        self.assertIn("no pseudonym salt", cm.exception.message)
        sources.capture(other, self.agent, file=str(self.page), author="MuseScore BVBA", author_as_given=True)
        import shutil
        shutil.copytree(self.lab.root / "private", other.root / "private", dirs_exist_ok=True)
        again = sources.capture(other, self.agent, file=str(self.page), author="jojo")
        self.assertEqual(again["body"]["author"], src["body"]["author"])
        self.assertTrue(verify(other)["ok"], verify(other)["errors"])
        self.assertFalse([n for n in verify(other)["notes"] if "private" in n or "salt" in n])

    def test_a_changed_private_file_breaks_L24_where_it_is_present(self):
        folder = sources.source_dir(self.lab, self.capture())
        (folder / "text.txt").write_text("edited", encoding="utf-8")
        self.assertEqual([e["rule"] for e in verify(self.lab)["errors"]], ["L24"])

    def test_a_correction_keeps_a_private_source_private(self):
        old = self.capture()
        new = sources.correct(self.lab, self.agent, old["id"], archive_uri="https://web.archive.org/web/2026/x")
        self.assertTrue((self.lab.root / "private" / "sources" / new["id"] / "thread.html").is_file())
        with self.assertRaises(LabError) as cm:
            sources.correct(self.other_checkout(), self.agent, old["id"], published_at="2014-12-01")
        self.assertIn("copy lab/private/", cm.exception.message)

    def test_records_begun_before_v0_19_keep_their_committed_files_and_salt(self):
        import shutil
        src = self.capture(author="jojo")
        shutil.move(str(self.lab.root / "private" / "sources" / "SR1"), str(self.lab.root / "sources" / "SR1"))
        shutil.move(str(self.lab.root / "private" / "pseudonym-salt"), str(self.lab.root / "pseudonym-salt"))
        self.assertEqual(sources.source_dir(self.lab, src), self.lab.root / "sources" / "SR1")
        self.assertIn("I need this too", sources.text_of(self.lab, src))
        self.assertEqual(sources.capture(self.lab, self.agent, file=str(self.page), author="jojo")["body"]["author"],
                         src["body"]["author"])
        rep = verify(self.lab)
        self.assertTrue(rep["ok"], rep["errors"])
        note = " | ".join(rep["notes"])
        self.assertIn("private sources SR1 have their files committed in lab/sources/", note)
        self.assertIn("the pseudonym salt is committed", note)


class TestSourcesAndPlans(SourceCase):
    def setUp(self):
        super().setUp()
        self.h = self.hyp()
        self.script = Path(self.tmp.name) / "count.py"
        self.script.write_text(COUNT)

    def plan(self):
        return self.add("plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "d", "analysis": "a",
                                 "decision": {"framework": "deterministic",
                                              "quantities": {"n": {"quantity": "threads asking for arranging"}},
                                              "supported": "n >= 1", "not_supported": "n < 1"}})

    def flags(self, plan, src):
        path = sources.source_dir(self.lab, src) / "text.txt"
        out = run_command(self.lab, self.agent, [sys.executable, str(self.script), str(path)], plan=plan,
                          data=[str(path)], echo=False, cwd=self.tmp.name)
        st = self.lab.state()
        return plan_flags(st, st.plan_rule_version(plan), [out["run"]])

    def test_a_source_captured_after_the_plan_is_flagged(self):
        s1 = self.capture()
        p1 = self.plan()
        s2 = self.capture(url="https://musescore.org/en/node/291410")
        p2 = self.plan()
        self.assertIn("sources_captured_after_plan", self.flags(p1, s2))
        self.assertNotIn("sources_captured_after_plan", self.flags(p2, s1))


class TestExport(SourceCase):
    def test_private_and_public_sources_and_their_licences(self):
        self.capture(retrieved_at="2026-11-05", title="Arrange for", archive_uri="https://web.archive.org/x")
        self.capture(url="https://github.com/musescore/MuseScore/issues/1", visibility="public",
                     terms="GitHub Terms of Service")
        self.capture(url=None, holder="study", licence="CC-BY-4.0", title="Coding notes")
        self.add("status", {"target": self.hyp(), "to": "abandoned", "rationale": "r"})
        out = export(self.lab)
        arts = {out["mapping"][a["id"]]: a for a in out["draft"]["artefacts"] if out["mapping"].get(a["id"], "")
                .startswith("SR")}
        private, public, own = arts["SR1"], arts["SR2"], arts["SR3"]
        self.assertEqual(private["availability"]["level"], "author_only")
        self.assertEqual(private["availability"]["content_hash"], private["version"])
        self.assertEqual(private["uri"], "https://musescore.org/en/node/42911")
        self.assertNotIn("licence", private)                               # never the paper's
        self.assertIn("saved by hand on 2026-11-05", private["description"])
        self.assertTrue(public["uri"].endswith("sources/SR2/thread.html"))
        self.assertIn("shared under: GitHub Terms of Service", public["description"])
        self.assertEqual((own["licence"], own["availability"]["level"]), ("CC-BY-4.0", "public"))
        self.assertNotIn("reference", own)
        refs = {r["id"]: r for r in out["draft"]["references"]}
        self.assertEqual(refs[private["reference"]], {"id": private["reference"], "id_type": "url",
                                                      "target": "https://musescore.org/en/node/42911",
                                                      "archive_uri": "https://web.archive.org/x"})
        todo = " | ".join(f"{t['where']}: {t['what']}" for t in out["completion"])
        self.assertIn(f"references.{public['reference']}.archive_uri", todo)
        self.assertIn(f"artefacts.{public['id']}.licence", todo)           # published: needs its terms or licence
        self.assertNotIn(f"artefacts.{private['id']}.licence", todo)


class TestCaptureCli(TestCli):
    def test_capture_and_quote(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        page = Path(self.tmp.name) / "thread.html"
        page.write_text(PAGE, encoding="utf-8")
        code, out, err = self.run_cli("lab", "capture", str(page), "--uri", "https://musescore.org/en/node/42911",
                                      "--retrieved-at", "2026-11-05T11:00:00Z", "--venue", "musescore.org")
        self.assertEqual(code, 0, err)
        self.assertIn("SR1", out)
        code, out, err = self.run_cli("lab", "quote", "SR1", "I need this too")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["exact"], "I need this too")


if __name__ == "__main__":
    unittest.main()
