"""Studies and papers in a repository (lab record spec v0.6 §3, §5.17-5.18, L18-L19; claimset spec v0.19 §2.4,
rules 55-57)."""

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from claimset import cli
from claimset.lab import anchor as anchor_mod
from claimset.lab import guard, layout
from claimset.lab.core import Lab, LabError, verify
from claimset.validate import LOCAL_RE, source_name, validate, validate_path

from test_lab_protect import HAVE_GIT, git, make_repo

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class InRepo(unittest.TestCase):
    """A temporary directory as working directory, without lab environment variables."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.top = Path(self.tmp.name).resolve()
        self.old_cwd = os.getcwd()
        self.old_env = {k: os.environ.pop(k, None) for k in ("CLAIMSET_LAB", "CLAIMSET_STUDY", "CLAIMSET_ACTOR")}
        os.chdir(self.top)

    def tearDown(self):
        os.chdir(self.old_cwd)
        for k, v in self.old_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue().strip(), err.getvalue().strip()

    def study(self, name, title="T"):
        code, out, err = self.cli("lab", "init", "--study", name, "--title", title)
        self.assertEqual(code, 0, err)
        lab = Lab(self.top / "claimset" / name / "lab")
        human = lab.append("actor", {"kind": "human", "name": "R", "orcid": "0000-0000-0000-0000"}, by="AC1")["id"]
        return lab, human


class TestLayout(InRepo):
    def test_init_creates_the_study_folder_and_records_its_name(self):
        lab, _ = self.study("eigen-checker")
        self.assertEqual(lab.read()[0]["body"]["study"], "eigen-checker")
        self.assertEqual(lab.state().study, "eigen-checker")

    def test_init_needs_a_study_or_a_lab(self):
        code, _, err = self.cli("lab", "init", "--title", "T")
        self.assertEqual(code, 1)
        self.assertIn("--study", err)
        code, _, err = self.cli("lab", "init", "--study", "Eigen_Checker", "--title", "T")
        self.assertEqual(code, 1)
        self.assertIn("not a valid study name", err)

    def test_finding_the_lab(self):
        self.study("one")
        self.assertEqual(layout.find_lab(), self.top / "claimset" / "one" / "lab")
        self.study("two")
        with self.assertRaises(LabError) as cm:
            layout.find_lab()
        self.assertIn("several studies (one, two)", cm.exception.message)
        self.assertEqual(layout.find_lab(study="two"), self.top / "claimset" / "two" / "lab")
        os.environ["CLAIMSET_STUDY"] = "one"
        self.assertEqual(layout.find_lab(), self.top / "claimset" / "one" / "lab")
        code, out, _ = self.cli("lab", "question", "Q?", "--by", "AC1", "--study", "two")
        self.assertEqual((code, out), (0, "Q1"))

    def test_build_files_leave_the_studies_out(self):
        (self.top / ".Rbuildignore").write_text("^\\.git$\n", encoding="utf-8")
        (self.top / "MANIFEST.in").write_text("include README\n", encoding="utf-8")
        (self.top / ".npmignore").write_text("claimset/\n", encoding="utf-8")       # already there
        code, out, _ = self.cli("lab", "init", "--study", "s1", "--title", "T")
        self.assertIn("'^claimset$' to .Rbuildignore", out)
        self.assertIn("'prune claimset' to MANIFEST.in", out)
        self.assertNotIn("'claimset/' to .npmignore", out)                           # already there
        self.assertIn("'claimset-code/' to .npmignore", out)
        self.assertNotIn(".gitattributes", out)                                       # not in this repository
        self.assertEqual((self.top / ".Rbuildignore").read_text(), "^\\.git$\n^claimset$\n^claimset-code$\n")
        self.assertEqual(layout.missing_ignores(self.top), [])
        (self.top / "MANIFEST.in").write_text("include README\n", encoding="utf-8")
        notes = verify(Lab(self.top / "claimset" / "s1" / "lab"))["notes"]
        self.assertTrue(any("MANIFEST.in" in n and "prune claimset" in n for n in notes), notes)

    def test_rbuildignore_patterns_that_already_match(self):
        (self.top / ".Rbuildignore").write_text("^claim.*$\n", encoding="utf-8")
        self.assertEqual(layout.missing_ignores(self.top), [])

    def test_lab_in_the_wrong_study_folder(self):
        lab, _ = self.study("right")
        (self.top / "claimset" / "right").rename(self.top / "claimset" / "wrong")
        rep = verify(Lab(self.top / "claimset" / "wrong" / "lab"))
        self.assertIn("L19", [e["rule"] for e in rep["errors"]])

    def test_guard_protects_every_study(self):
        self.study("one")
        self.study("two")
        for path in ("claimset/one/lab/lab.jsonl", "claimset/two/lab/runs/RN1/x"):
            self.assertIsNotNone(guard.decide({"tool_name": "Edit", "tool_input": {"file_path": path},
                                               "cwd": str(self.top)}), path)
        self.assertIsNotNone(guard.decide({"tool_name": "Bash", "tool_input": {"command": "rm -r claimset/two/lab"},
                                           "cwd": str(self.top)}))
        self.assertIsNone(guard.decide({"tool_name": "Edit", "tool_input": {"file_path": "claimset/two/code/a.py"},
                                        "cwd": str(self.top)}))


class TestPapers(InRepo):
    def setUp(self):
        super().setUp()
        self.lab, self.h = self.study("eigen-checker")
        os.environ["CLAIMSET_ACTOR"] = self.h
        for q in ("Which criterion?", "How fast?"):
            self.assertEqual(self.cli("lab", "question", q)[0], 0)

    def test_a_question_is_reported_in_one_paper(self):
        self.assertEqual(self.cli("lab", "paper", "eigen-checker", "--questions", "Q1")[1], "PA1")
        code, _, err = self.cli("lab", "paper", "second", "--questions", "Q1", "Q2")
        self.assertEqual(code, 1)
        self.assertIn("[L18]", err)
        self.assertIn("Q1 already belong to paper 'eigen-checker'", err)
        code, _, err = self.cli("lab", "paper", "eigen-checker", "--questions", "Q2")
        self.assertIn("already the name", err)
        self.assertEqual(self.cli("lab", "paper", "eigen-checker-cpp", "--questions", "Q2")[1], "PA2")
        # moving a question: correct the papers
        self.assertEqual(self.cli("lab", "paper", "eigen-checker", "--questions", "Q1", "Q2",
                                  "--supersedes", "PA1")[0], 1)
        self.assertEqual(self.cli("lab", "paper", "eigen-checker-cpp", "--questions", "Q2", "--supersedes", "PA2",
                                  "--title", "C++")[0], 0)
        self.assertTrue(verify(self.lab)["ok"])

    def test_bad_paper_names_and_questions(self):
        self.assertIn("valid paper name", self.cli("lab", "paper", "Eigen", "--questions", "Q1")[2])
        self.assertIn("Q9", self.cli("lab", "paper", "pp", "--questions", "Q9")[2])

    def answer(self, question, statement, to):
        hid = self.lab.append("hypothesis", {"statement": statement, "question": question, "origin": "a_priori"},
                              by=self.h)["id"]
        body = {"target": hid, "to": to, "rationale": "r"}
        if to == "inconclusive":
            body["evidence"] = [question]
        self.lab.append("status", body, by=self.h)
        return hid

    def test_unreported_results_are_named(self):
        self.answer("Q1", "A.", "abandoned")
        h2 = self.answer("Q2", "B.", "inconclusive")
        self.assertEqual(verify(self.lab)["notes"], [])           # no papers yet: the record is one paper
        self.cli("lab", "paper", "eigen-checker", "--questions", "Q1")
        notes = verify(self.lab)["notes"]
        self.assertEqual(notes, [f"{h2} has a result but its question Q2 is in no paper: it is reported nowhere"])

    def test_export_by_paper(self):
        self.answer("Q1", "A holds.", "abandoned")
        self.answer("Q2", "B holds.", "inconclusive")
        self.cli("lab", "paper", "eigen-checker", "--questions", "Q1", "--title", "The criterion")
        self.cli("lab", "paper", "eigen-checker-cpp", "--questions", "Q2")
        code, _, err = self.cli("lab", "export")
        self.assertEqual(code, 1)
        self.assertIn("several papers", err)
        code, out, err = self.cli("lab", "export", "--paper", "eigen-checker-cpp")
        self.assertEqual(code, 0, err)
        path = self.top / "claimset" / "eigen-checker" / "eigen-checker-cpp.claimset.json"
        draft = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(draft["paper"]["study"], "eigen-checker")
        self.assertEqual(len(draft["claims"]), 1)
        exp = draft["paper"]["exploration"]
        self.assertEqual(exp["questions"], 1)
        self.assertEqual(exp["hypotheses"]["total"], 1)
        self.assertEqual(exp["covers"]["questions"], ["Q2"])
        self.assertEqual(exp["covers"]["lab"]["hypotheses"]["total"], 2)
        self.assertEqual(exp["covers"]["other_papers"], [{"name": "eigen-checker", "questions": ["Q1"]}])
        self.assertNotIn("55", {e["rule"] for e in validate(draft).errors})
        code, _, err = self.cli("lab", "export", "--paper", "eigen-checker-cpp")
        self.assertIn("--force", err)
        code, _, _ = self.cli("lab", "export", "--paper", "eigen-checker")
        draft = json.loads((path.parent / "eigen-checker.claimset.json").read_text(encoding="utf-8"))
        self.assertEqual(draft["paper"]["title"], "The criterion")
        self.assertEqual(draft["claims"], [])                      # its one hypothesis was abandoned


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TestMove(InRepo):
    def test_a_legacy_lab_moves_into_its_study_with_its_code(self):
        make_repo(self.top)
        lab = Lab.create(self.top / ".claude" / "lab", "Old study")
        human = lab.append("actor", {"kind": "human", "name": "R", "orcid": "0000-0000-0000-0000"}, by="AC1")["id"]
        q = lab.append("question", {"text": "Q?"}, by=human)["id"]
        lab.append("hypothesis", {"statement": "H.", "question": q, "origin": "a_priori"}, by=human)
        anchor_mod.save_config(lab, {"anchor": {"service": "git_remote", "remote": "origin"}})
        first = anchor_mod.anchor(lab)
        self.assertEqual(first["receipt"]["branch"], "claimset-lab")         # a record without a study
        (self.top / ".claude" / "research").mkdir(parents=True)
        (self.top / ".claude" / "research" / "proto.py").write_text("print(1)\n", encoding="utf-8")
        code, out, err = self.cli("lab", "move", "--lab", ".claude/lab", "--study", "old-study",
                                  "--also", ".claude/research=claimset/old-study/code", "--by", human)
        self.assertEqual(code, 0, err)
        moved = Lab(self.top / "claimset" / "old-study" / "lab")
        ev = moved.read()[-1]
        self.assertEqual(ev["type"], "relocation")
        self.assertEqual(ev["body"], {"from": ".claude/lab", "to": "claimset/old-study/lab", "study": "old-study",
                                      "moved": [{"from": ".claude/research", "to": "claimset/old-study/code"}]})
        self.assertFalse((self.top / ".claude" / "lab").exists())
        self.assertTrue((self.top / "claimset" / "old-study" / "code" / "proto.py").exists())
        self.assertEqual(moved.state().study, "old-study")
        # anchoring stays on the branch the record was anchored on, and the remote check finds the old path
        self.assertEqual(anchor_mod.load_config(moved)["anchor"]["branch"], "claimset-lab")
        anchor_mod.anchor(moved)
        rep = verify(moved, remote=True)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertEqual(layout.find_lab(), self.top / "claimset" / "old-study" / "lab")

    def test_new_study_anchors_on_its_own_branch(self):
        make_repo(self.top)
        lab, _ = self.study("alpha")
        anchor_mod.save_config(lab, {"anchor": {"service": "git_remote", "remote": "origin"}})
        self.assertEqual(anchor_mod.anchor(lab)["receipt"]["branch"], "claimset-lab/alpha")

    def test_move_refuses_a_full_destination(self):
        lab, h = self.study("alpha")
        (self.top / "elsewhere").mkdir()
        (self.top / "elsewhere" / "x").write_text("x", encoding="utf-8")
        code, _, err = self.cli("lab", "move", "--to", "elsewhere", "--by", h)
        self.assertEqual(code, 1)
        self.assertIn("not empty", err)
        self.assertTrue(lab.log_path.exists())


class TestValidatorRules(unittest.TestCase):
    def setUp(self):
        self.d = load("submission-from-lab.json")

    def errors(self, doc, **kw):
        return {e["rule"] for e in validate(doc, **kw).errors}

    def covers(self):
        exp = self.d["paper"]["exploration"]
        h, t = copy.deepcopy(exp["hypotheses"]), copy.deepcopy(exp["tests"])
        lab_h = dict(h, total=h["total"] + 1, open=h["open"] + 1)
        exp["questions"] = 1
        exp["covers"] = {"questions": ["Q1"], "lab": {"questions": 2, "hypotheses": lab_h, "tests": t},
                         "other_papers": [{"name": "second-paper", "questions": ["Q2"]}]}
        return exp

    def test_55_valid_covers(self):
        self.covers()
        self.assertNotIn("55", self.errors(self.d))

    def test_55_broken_covers(self):
        exp = self.covers()
        exp["covers"]["other_papers"][0]["questions"] = ["Q1"]               # Q1 in two papers
        exp["covers"]["lab"]["hypotheses"]["total"] = 0                      # fewer than the paper's
        exp["questions"] = 3
        self.assertIn("55", self.errors(self.d))

    def test_56_study_name(self):
        self.d["paper"]["study"] = "eigen-checker"
        self.assertNotIn("56", self.errors(self.d))
        self.d["paper"]["study"] = "Eigen Checker"
        self.assertIn("56", self.errors(self.d))

    def local(self, target):
        claim = next(c for c in self.d["claims"] if c["id"] == "C2")
        claim["relations"] = [{"type": "extends", "target": target, "id_type": "claimset_local",
                               "note": "The earlier paper introduced the criterion."}]

    def test_57_local_reference_format_and_publication(self):
        self.local("eigen-checker/eigen-checker.claimset.json#C3")
        self.assertNotIn("57", self.errors(self.d))
        self.local("../eigen-checker.json")
        self.assertIn("57", self.errors(self.d))
        self.local("eigen-checker/eigen-checker.claimset.json#C3")
        pub = load("published-native.json")
        next(c for c in pub["claims"] if c["id"] == "C2")["relations"] = \
            self.d["claims"][[c["id"] for c in self.d["claims"]].index("C2")]["relations"]
        self.assertIn("57", self.errors(pub, published=True))

    def test_57_local_reference_resolved_in_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "claimset"
            (folder / "eigen-checker").mkdir(parents=True)
            (folder / "eigen-checker" / "eigen-checker.claimset.json").write_text(
                json.dumps(load("submission-native.json")), encoding="utf-8")
            self.local("eigen-checker/eigen-checker.claimset.json#C2")
            self.assertNotIn("57", self.errors(self.d, local_root=folder))
            self.local("eigen-checker/eigen-checker.claimset.json#C9")
            self.assertIn("57", self.errors(self.d, local_root=folder))
            self.local("eigen-checker/other.claimset.json")
            self.assertIn("57", self.errors(self.d, local_root=folder))


class TestConvertedNames(unittest.TestCase):
    """§2.4: a converted paper in a claimset/ folder is named from its source's identifier (warning 31)."""

    def test_source_names(self):
        def name(kind, sid, version=None):
            return source_name({"id_type": kind, "source_id": sid, "source_version": version})
        self.assertEqual(name("doi", "10.1016/j.ejor.2022.04.040"), "doi-10-1016-j-ejor-2022-04-040")
        self.assertEqual(name("doi", "https://doi.org/10.1093/IMAMAN/dpad028"), "doi-10-1093-imaman-dpad028")
        self.assertEqual(name("arxiv", "arXiv:2401.01234", "v2"), "arxiv-2401-01234v2")
        self.assertEqual(name("arxiv", "2401.01234v2", "v2"), "arxiv-2401-01234v2")
        self.assertEqual(name("arxiv", "math/0501234", "v1"), "arxiv-math-0501234v1")
        self.assertEqual(name("doi", "10.1002/(SICI)1099-131X(199603)15:2<91::AID-FOR614>3.0.CO;2-6"),
                         "doi-10-1002-sici-1099-131x-199603-15-2-91-aid-for614-3-0-co-2-6")
        self.assertIsNone(name("doi", ""))

    def warnings(self, file_name):
        from test_validate import converted_base
        doc = converted_base()                          # arXiv 2601.00000, version v2
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claimset" / "pilot" / file_name
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(doc), encoding="utf-8")
            rep = validate_path(path)
        self.assertEqual(rep.errors, [])
        return {w["rule"] for w in rep.warnings}

    def test_w31_named_from_the_source(self):
        self.assertNotIn("W31", self.warnings("arxiv-2601-00000v2.claimset.json"))

    def test_w31_named_otherwise(self):
        self.assertIn("W31", self.warnings("2601.00000.claimset.json"))

    def test_w31_only_in_a_claimset_folder_and_for_converted_papers(self):
        from test_validate import converted_base
        self.assertNotIn("W31", {w["rule"] for w in validate(converted_base(), file_name="x.claimset.json").warnings})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claimset" / "eigen-checker" / "eigen-checker.claimset.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(load("submission-native.json")), encoding="utf-8")
            self.assertNotIn("W31", {w["rule"] for w in validate_path(path).warnings})

    def test_long_converted_names_can_be_cited_locally(self):
        long = "doi-10-1002-sici-1099-131x-199603-15-2-91-aid-for614-3-0-co-2-6"
        self.assertRegex(f"pilot/{long}.claimset.json#C3", LOCAL_RE)


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TestTag(InRepo):
    def test_versions_are_tagged_not_renamed(self):
        make_repo(self.top)
        paper = self.top / "claimset" / "eigen-checker" / "eigen-checker.claimset.json"
        paper.parent.mkdir(parents=True)
        paper.write_text(json.dumps(load("submission-native.json"), indent=1), encoding="utf-8")
        code, _, err = self.cli("tag", str(paper))
        self.assertEqual(code, 1)
        self.assertIn("not committed", err)
        git("add", "-A", cwd=self.top)
        git("commit", "-qm", "paper", cwd=self.top)
        code, out, err = self.cli("tag", str(paper))
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("claimset/eigen-checker/eigen-checker/v1"))
        paper.write_text(paper.read_text() + "\n", encoding="utf-8")
        self.assertIn("uncommitted", self.cli("tag", str(paper))[2])
        git("commit", "-qam", "revised", cwd=self.top)
        self.assertTrue(self.cli("tag", str(paper))[1].startswith("claimset/eigen-checker/eigen-checker/v2"))
        self.assertIn("not a paper of a study", self.cli("tag", str(self.top / "x.json"))[2])


if __name__ == "__main__":
    unittest.main()
