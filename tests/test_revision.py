"""Revisions of published claimsets (revision spec v0.1; claimset spec v0.21 rules 25 and 59; lab record v0.9
§5.7.7). The record is built from the published example: version 1, its initial review, a revision with its
response, and the re-review of the revision."""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from claimset import cli
from claimset import revision as rv
from claimset.validate import validate

ROOT = Path(__file__).resolve().parents[1]
DOI = "doi:10.0000/claimset.2026.000123"
V1 = f"{DOI}@v1"


def published_v1():
    return json.loads((ROOT / "examples" / "published-native.json").read_text(encoding="utf-8"))


TEST_RHO = {"procedure": "Compute the correlation of method M's forecast errors between every pair of the 1,200 "
                         "series of E1; report the largest absolute value.",
            "framework": "deterministic", "parameters": {},
            "quantities": {"rho": {"quantity": "largest absolute cross-series correlation of forecast errors"}},
            "upheld": "rho > 0.3", "withdrawn": "rho <= 0.3"}


NOVELTY_C2 = {"claim": "C2", "closest": [], "searched": {"sources": ["claimset", "openalex"], "date": "2026-10-23"},
              "comments": []}
PRIOR = {"id": "c4", "claims": ["C2"], "category": "novelty",
         "comment": "A benchmark of method M against the seasonal naive method on UK retail series is reported in B9.",
         "requested_action": "Relate C2 to B9 and relabel it, or state how it differs.",
         "references": [{"target": "doi:10.1016/j.ijforecast.2020.00001", "id_type": "doi", "locator": "Table 3"}],
         "resolution_test": {"procedure": "Read Table 3 of doi:10.1016/j.ijforecast.2020.00001.",
                             "framework": "documentary",
                             "upheld": "it reports method M improving on seasonal naive for UK monthly retail sales",
                             "withdrawn": "it does not"}}


def review_rv1(v1):
    return {
        "schema": "claimset-review", "schema_version": "0.1", "id": "RV1", "kind": "initial",
        "target": {"version": V1, "hash": rv.doc_hash(v1)},
        "reviewer": {"type": "agent", "name": "Claimset reviewer",
                     "models": [{"provider": "Other provider", "name": "Reviewer model", "version": "2",
                                 "identifier": "reviewer-model-2"}],
                     "prompt": {"version": "review-0.1", "uri": "https://example.org/prompts/review-0.1.md",
                                "hash": "sha256:" + "a" * 64},
                     "system": {"name": "claimset-review", "version": "0.1"},
                     "rubric": {"version": "rubric-0.1", "uri": "https://example.org/rubric-0.1.md",
                                "hash": "sha256:" + "b" * 64}},
        "date": "2026-10-23",
        "comments": [
            {"id": "c1", "claims": ["C2"], "targets": ["C2.scope"], "category": "scope",
             "severity": "reduces_transferability",
             "comment": "The series of E1 come from one retailer, but the scope of C2 covers UK monthly retail sales "
                        "series in general.",
             "requested_action": "State the single retailer in the scope or as a limitation.",
             "resolution_test": {"procedure": "Read the data description of A1.", "framework": "documentary",
                                 "upheld": "A1 describes series from one retailer",
                                 "withdrawn": "A1 describes series from several retailers"}},
            {"id": "c2", "claims": ["C3", "C2"], "targets": ["C3"], "category": "assumption",
             "severity": "could_reverse",
             "comment": "C3 assumes independent forecast errors across series, but series of one retailer share "
                        "promotions; C2's interval rests on C3.",
             "requested_action": "Measure the cross-series correlation of the errors.",
             "resolution_test": TEST_RHO},
            {"id": "c3", "claims": ["C2"], "targets": ["C2.statement"], "category": "presentation",
             "severity": "presentation",
             "comment": "The statement of C2 does not name the country its scope is limited to.",
             "requested_action": "Name the United Kingdom in the statement."},
        ],
        "verdicts": [],
        "scores": [
            {"claim": "C1", "score": 100, "comments": []},
            {"claim": "C3", "score": 50, "comments": [f"{V1}/RV1.c2"],
             "reason": "Independence across series is unchecked and doubtful for one retailer's series (c2)."},
            {"claim": "C2", "score": 60, "comments": [f"{V1}/RV1.c1", f"{V1}/RV1.c2", f"{V1}/RV1.c3"],
             "reason": "The gain may not transfer beyond one retailer (c1), and its interval rests on C3 (c2)."}],
        "novelty": [NOVELTY_C2],
        "lab_record": None}


def revision_v2(v1):
    d = rv._content(v1)
    d["paper"]["id"] = DOI
    claims = {c["id"]: c for c in d["claims"]}
    claims["C2"]["statement"] = ("[[D2:Method M]] improves [[D1:forecast skill]] over the [[D3:seasonal naive "
                                 "method]] on every store network series of the [[D4:UK monthly retail sales]].")
    claims["C2"]["limitations"] = ["L1", "L2"]
    claims["C3"]["check"] = "tested"
    claims["C3"]["check_evidence"] = "E2"
    d["limitations"].append({"id": "L2", "category": "sampling", "statement": "The series of E1 share the "
                             "promotions of one retailer, so the gain of method M is measured under one promotion "
                             "calendar.", "affects": ["C2"], "impact": "reduces_transferability", "mitigation": "none"})
    d["evidence"].append({"id": "E2", "profile": "quant", "description": "Pairwise correlation of method M's "
                          "forecast errors across the 1,200 series of E1.", "design": "other",
                          "setup": {"studied": ["D2"], "cases": ["D4"]},
                          "results": [{"id": "R1", "variable": "largest absolute cross-series correlation of "
                                                               "forecast errors", "value": 0.12}],
                          "artefacts": ["A1"], "availability": {"level": "public"}})
    return d


def response_rp1(v1, rv1):
    return {"schema": "claimset-response", "schema_version": "0.1", "id": "RP1",
            "revises": {"version": V1, "hash": rv.doc_hash(v1)},
            "responds_to": [{"review": f"{V1}/RV1", "hash": rv.doc_hash(rv1)}],
            "entries": [
                {"comment": f"{V1}/RV1.c1", "response": "accepted_as_limitation", "limitation": "L2",
                 "changes": ["C2.limitations"]},
                {"comment": f"{V1}/RV1.c2", "response": "rebutted", "evidence": ["E2"],
                 "changes": ["C3.check", "C3.check_evidence", "E2"],
                 "argument": "The largest cross-series correlation is 0.12, below the test's 0.3."},
                {"comment": f"{V1}/RV1.c3", "response": "changed", "changes": ["C2.statement"]}],
            "other_changes": []}


def publish(record_dir, n, submission, previous, response):
    """What the platform does at publication (claimset §2.2, revision §5.3), enough for the record."""
    d = copy.deepcopy(submission)
    paper = d["paper"]
    paper.update(id=DOI, version=n, previous_version=f"{DOI}@v{n - 1}", status="active", date="2026-10-30",
                 changes=[op for op, _ in rv.patch(previous, submission, response)],
                 answers=f"{DOI}@v{n}/{response['id']}")
    paper.setdefault("classification", {}).update({k: previous["paper"]["classification"][k]
                                                   for k in ("subfield", "topics")})
    rep = validate(d, published=True)
    for c in d["claims"]:
        c.update(rep.computed[c["id"]])
    folder = Path(record_dir) / f"v{n}"
    folder.mkdir(parents=True)
    (folder / "claimset.json").write_text(json.dumps(d), encoding="utf-8")
    (folder / f"response-{response['id']}.json").write_text(json.dumps(response), encoding="utf-8")
    return d


def write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def rules(rep):
    return sorted({e["rule"] for e in rep.errors})


class RecordCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "10.0000_claimset.2026.000123"
        self.v1 = published_v1()
        self.rv1 = review_rv1(self.v1)
        write(self.dir / "v1" / "claimset.json", self.v1)
        write(self.dir / "v1" / "reviews" / "RV1.json", self.rv1)
        self.v2 = revision_v2(self.v1)
        self.rp1 = response_rp1(self.v1, self.rv1)

    def record(self):
        return rv.Record(self.dir)

    def check(self, v2=None, rp1=None, lab=None):
        return rv.check_revision(self.record(), v2 or self.v2, rp1 or self.rp1, lab=lab)[0]

    def publish_v2(self):
        self.pub2 = publish(self.dir, 2, self.v2, self.v1, self.rp1)
        return self.pub2

    def re_review(self, **extra):
        doc = {"schema": "claimset-review", "schema_version": "0.1", "id": "RV2", "kind": "re_review",
               "target": {"version": f"{DOI}@v2", "hash": rv.doc_hash(self.pub2)},
               "reviewer": self.rv1["reviewer"], "date": "2026-10-31", "comments": [],
               "verdicts": [
                   {"comment": f"{V1}/RV1.c1", "state": "limited", "reason": "L2 states the single retailer."},
                   {"comment": f"{V1}/RV1.c2", "state": "withdrawn", "reason": "E2 reports 0.12, below 0.3.",
                    "rule": {"met": "withdrawn", "quantities": {"rho": 0.12}}, "evidence": [f"{DOI}@v2#E2"]},
                   {"comment": f"{V1}/RV1.c3", "state": "resolved", "reason": "C2 names the United Kingdom."}],
               "scores": [{"claim": "C1", "score": 100}, {"claim": "C3", "score": 100},
                          {"claim": "C2", "score": 90, "comments": [f"{V1}/RV1.c1"],
                           "reason": "The gain is measured under one retailer's promotions, as L2 states (c1)."}],
               "novelty": [dict(NOVELTY_C2, searched={"sources": ["claimset"], "date": "2026-10-31"})]}
        doc.update(extra)
        return doc


class TestSpecificationDocuments(unittest.TestCase):
    def test_bundled(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["spec", "--revision"]), 0)
        self.assertTrue(out.getvalue().startswith("# Claimset Revision"))


class TestClaimsetRules(RecordCase):
    def test_rule_25_allows_paper_id_in_a_revision(self):
        self.assertEqual(rules(validate(self.v2)), ["25"])
        self.assertEqual(validate(self.v2, revision=True).errors, [])
        broken = copy.deepcopy(self.v2)
        broken["paper"]["correction_reason"] = "x"
        self.assertEqual(rules(validate(broken, revision=True)), ["25"])

    def test_rule_59(self):
        pub = self.publish_v2()
        self.assertEqual(validate(pub, published=True).errors, [])
        bad = copy.deepcopy(pub)
        bad["paper"]["answers"] = f"{DOI}@v1/RP1"
        self.assertEqual(rules(validate(bad, published=True)), ["59"])
        bad = copy.deepcopy(pub)
        bad["paper"]["changes"][0]["addresses"] = ["RV1.c3"]
        self.assertEqual(rules(validate(bad, published=True)), ["59"])

    def test_rule_59_test_addresses(self):
        d = json.loads((ROOT / "examples" / "submission-from-lab.json").read_text(encoding="utf-8"))
        c = next(c for c in d["claims"] if c.get("test"))
        c["test"]["addresses"] = [f"{V1}/RV1.c2"]
        self.assertEqual(validate(d).errors, [])
        c["test"]["addresses"] = ["RV1.c2"]
        self.assertEqual(rules(validate(d)), ["59"])


class TestReviews(RecordCase):
    def unfiled(self):
        """The record without RV1, which the test checks as a new review."""
        (self.dir / "v1" / "reviews" / "RV1.json").unlink()
        return self.record()
    def test_initial_review_is_valid(self):
        rep = rv.check_review(self.record(), self.rv1)      # as filed in the record
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])
        self.assertEqual(rv.check_review(self.unfiled(), self.rv1).errors, [])

    def test_r1_target_and_kind(self):
        bad = dict(self.rv1, id="RV3")      # a second initial review
        self.assertEqual(rules(rv.check_review(self.record(), bad)), ["R1"])
        bad = dict(self.rv1, target={"version": V1, "hash": "sha256:" + "0" * 64})
        rec = self.unfiled()
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R1"])

    def test_r2_reviewer(self):
        rec = self.unfiled()
        bad = copy.deepcopy(self.rv1)
        del bad["reviewer"]["prompt"]["hash"]
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R2"])
        human = dict(self.rv1, id="RV9", kind="additional", reviewer={"type": "human", "name": "R"}, scores=[],
                     novelty=[])
        self.assertEqual(rules(rv.check_review(rec, human)), ["R2"])

    def test_w_r1_same_model_as_an_author(self):
        same = copy.deepcopy(self.rv1)
        same["reviewer"]["models"] = [dict(self.v1["models"][0])]
        rep = rv.check_review(self.unfiled(), same)
        self.assertEqual([w["rule"] for w in rep.warnings], ["W-R1"])

    def test_r3_comments(self):
        rec = self.unfiled()
        for change in ({"claims": []}, {"claims": ["C9"]}, {"targets": ["E9"]}, {"category": "style"},
                       {"severity": "minor"}, {"requested_action": ""}):
            bad = copy.deepcopy(self.rv1)
            bad["comments"][2].update(change)
            self.assertIn("R3", rules(rv.check_review(rec, bad)), change)

    def test_r4_resolution_test(self):
        rec = self.unfiled()
        for change in ({"withdrawn": ""}, {"upheld": "rho >> 0.3"}, {"procedure": ""}, {"framework": "vote"},
                       {"upheld": "sigma > 0.3"}):
            bad = copy.deepcopy(self.rv1)
            bad["comments"][1]["resolution_test"] = dict(TEST_RHO, **change)
            self.assertEqual(rules(rv.check_review(rec, bad)), ["R4"], change)
        bad = copy.deepcopy(self.rv1)
        del bad["comments"][0]["resolution_test"]
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R4"])

    def test_re_review(self):
        self.publish_v2()
        self.assertEqual(rv.check_review(self.record(), self.re_review()).errors, [])

    def test_r5_every_unresolved_comment_gets_a_verdict(self):
        self.publish_v2()
        doc = self.re_review()
        doc["verdicts"].pop()
        self.assertEqual(rules(rv.check_review(self.record(), doc)), ["R5"])

    def test_r6_new_comments_on_changes_only(self):
        self.publish_v2()
        on_changed = {"id": "c1", "claims": ["C2"], "targets": ["C2.statement"], "category": "presentation",
                      "severity": "presentation", "comment": "c", "requested_action": "a"}
        self.assertEqual(rv.check_review(self.record(), self.re_review(comments=[on_changed])).errors, [])
        on_unchanged = dict(on_changed, claims=["C1"], targets=["C1"])
        self.assertEqual(rules(rv.check_review(self.record(), self.re_review(comments=[on_unchanged]))), ["R6"])
        reversing = dict(on_unchanged, category="assumption", severity="could_reverse", resolution_test=TEST_RHO)
        self.assertEqual(rv.check_review(self.record(), self.re_review(comments=[reversing])).errors, [])

    def test_r7_verdicts(self):
        self.publish_v2()
        doc = self.re_review()
        doc["verdicts"][1] = {"comment": f"{V1}/RV1.c2", "state": "disputed", "reason": "r"}
        self.assertEqual(rules(rv.check_review(self.record(), doc)), ["R7"])
        doc["verdicts"][1]["replacement_test"] = TEST_RHO
        self.assertEqual(rules(rv.check_review(self.record(), doc)), ["R7"])       # must differ
        doc["verdicts"][1]["replacement_test"] = dict(TEST_RHO, upheld="rho > 0.2", withdrawn="rho <= 0.2")
        self.assertEqual(rv.check_review(self.record(), doc).errors, [])
        doc = self.re_review()
        del doc["verdicts"][1]["evidence"]
        self.assertEqual(rules(rv.check_review(self.record(), doc)), ["R7"])

    def test_r18_every_claim_scored_in_initial_and_re_reviews(self):
        rec = self.unfiled()
        bad = copy.deepcopy(self.rv1)
        bad["scores"].pop(0)
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R18"])
        bad["scores"] = []
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R18"])

    def test_r18_no_deduction_without_a_comment(self):
        rec = self.unfiled()
        for change in ({"score": 60, "comments": []}, {"score": 60, "reason": ""}, {"score": 101},
                       {"score": 7.5}, {"comments": [f"{V1}/RV1.c9"]},
                       {"score": 100, "comments": [f"{V1}/RV1.c1"], "reason": ""}):
            bad = copy.deepcopy(self.rv1)
            bad["scores"][2].update(change)
            self.assertEqual(rules(rv.check_review(rec, bad)), ["R18"], change)
        bad = copy.deepcopy(self.rv1)
        bad["scores"][0] = {"claim": "C1", "score": 80, "comments": [f"{V1}/RV1.c1"], "reason": "r"}
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R18"])      # c1 names C2, which C1 does not rest on

    def test_r18_comments_on_what_a_claim_rests_on_count(self):
        # c2 names C3 (and C2); a comment naming only C3 may still lower C2, which rests on C3
        rec = self.unfiled()
        doc = copy.deepcopy(self.rv1)
        doc["comments"][1]["claims"] = ["C3"]
        self.assertEqual(rv.check_review(rec, doc).errors, [])

    def test_r18_settled_comments_no_longer_lower_scores(self):
        self.publish_v2()
        doc = self.re_review()
        doc["scores"][1] = {"claim": "C3", "score": 70, "comments": [f"{V1}/RV1.c2"], "reason": "r"}
        self.assertEqual(rules(rv.check_review(self.record(), doc)), ["R18"])     # c2 is withdrawn

    def test_r18_rubric_and_who_scores(self):
        rec = self.unfiled()
        bad = copy.deepcopy(self.rv1)
        del bad["reviewer"]["rubric"]
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R2"])
        human = dict(copy.deepcopy(self.rv1), id="RV9", kind="additional", novelty=[],
                     reviewer={"type": "human", "name": "R", "orcid": "0000-0002-0000-0002",
                               "competing_interests": "none"})
        self.assertEqual(rules(rv.check_review(self.record(), human)), ["R18"])
        human["scores"] = []
        self.assertEqual(rv.check_review(self.record(), human).errors, [])
        extra = dict(copy.deepcopy(self.rv1), id="RV9", kind="additional",
                     comments=[dict(self.rv1["comments"][2], id="c1")],
                     scores=[{"claim": "C1", "score": 100}], novelty=[])
        self.assertEqual(rules(rv.check_review(self.record(), extra)), ["R18"])    # its comment names only C2

    def test_novelty_comments_have_no_severity_and_cite_the_work(self):
        rec = self.unfiled()
        doc = copy.deepcopy(self.rv1)
        doc["comments"].append(PRIOR)
        doc["novelty"] = [dict(NOVELTY_C2, comments=[f"{V1}/RV1.c4"],
                               closest=[{"target": "doi:10.1016/j.ijforecast.2020.00001", "id_type": "doi",
                                         "note": "Same benchmark design on another retailer's series."}])]
        self.assertEqual(rv.check_review(rec, doc).errors, [])
        bad = copy.deepcopy(doc)
        bad["comments"][3]["severity"] = "could_reverse"
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R3"])
        bad = copy.deepcopy(doc)
        del bad["comments"][3]["references"]
        self.assertEqual(rules(rv.check_review(rec, bad)), ["R3"])

    def test_r18_novelty_comments_do_not_lower_credibility(self):
        rec = self.unfiled()
        doc = copy.deepcopy(self.rv1)
        doc["comments"].append(PRIOR)
        doc["scores"][2]["comments"].append(f"{V1}/RV1.c4")
        self.assertEqual(rules(rv.check_review(rec, doc)), ["R18"])

    def test_r19_novelty_assessments(self):
        rec = self.unfiled()
        for change in ({"novelty": []},
                       {"novelty": [dict(NOVELTY_C2, claim="C1")]},                    # an assumption
                       {"novelty": [dict(NOVELTY_C2, searched={"sources": [], "date": "2026-10-23"})]},
                       {"novelty": [dict(NOVELTY_C2, searched={"sources": ["claimset"], "date": "23/10/2026"})]},
                       {"novelty": [dict(NOVELTY_C2, closest=[{"note": "n"}])]},
                       {"novelty": [dict(NOVELTY_C2, comments=[f"{V1}/RV1.c1"])]}):  # not a novelty comment
            bad = dict(copy.deepcopy(self.rv1), **change)
            self.assertEqual(rules(rv.check_review(rec, bad)), ["R19"], change)
        human = dict(copy.deepcopy(self.rv1), id="RV9", kind="additional", scores=[],
                     reviewer={"type": "human", "name": "R", "orcid": "0000-0002-0000-0002",
                               "competing_interests": "none"})
        self.assertEqual(rules(rv.check_review(self.record(), human)), ["R19"])
        human["novelty"] = []
        self.assertEqual(rv.check_review(self.record(), human).errors, [])

    def test_resolution(self):
        self.publish_v2()
        write(self.dir / "v2" / "reviews" / "RV2.json", self.re_review())
        res = {"schema": "claimset-resolution", "schema_version": "0.1", "id": "RL1",
               "target": {"version": f"{DOI}@v2", "hash": rv.doc_hash(self.pub2)},
               "resolver": {"name": "H", "orcid": "0000-0002-0000-0001", "competing_interests": "none"},
               "date": "2026-11-02",
               "verdicts": [{"comment": f"{V1}/RV1.c1", "state": "upheld", "reason": "A1 names one retailer.",
                             "rule": {"met": "upheld"}, "evidence": [f"{DOI}@v2#A1"]}]}
        self.assertEqual(rv.check_review(self.record(), res).errors, [])
        bad = copy.deepcopy(res)
        bad["verdicts"][0]["state"] = "resolved"
        self.assertEqual(rules(rv.check_review(self.record(), bad)), ["R7"])
        bad = copy.deepcopy(res)
        bad["resolver"]["orcid"] = self.v1["contributors"][1].get("orcid")
        self.assertEqual(rules(rv.check_review(self.record(), bad)), ["R2"])


class TestRevisionCheck(RecordCase):
    def test_valid_revision(self):
        rep, ops = rv.check_revision(self.record(), self.v2, self.rp1)
        self.assertEqual(rep.errors, [])
        self.assertEqual([w["rule"] for w in rep.warnings], ["W-R2"])     # E2 is not from a planned test
        self.assertEqual(rv.apply_patch(rv._content(self.v1), ops), rv._content(self.v2))
        fields = {op["path"]: op for op in ops}
        statement = next(op for op in ops if op["path"].endswith("/statement"))
        self.assertEqual(statement["addresses"], [f"{V1}/RV1.c3"])
        self.assertTrue(all(op.get("reason") for op in fields.values()))

    def test_r8_one_entry_per_unresolved_comment(self):
        bad = copy.deepcopy(self.rp1)
        bad["entries"].pop()
        self.assertEqual(rules(self.check(rp1=bad)), ["R15", "R8"])
        bad = copy.deepcopy(self.rp1)
        bad["responds_to"] = []
        self.assertEqual(rules(self.check(rp1=bad)), ["R8"])
        bad = copy.deepcopy(self.rp1)
        bad["revises"]["hash"] = "sha256:" + "0" * 64
        self.assertEqual(rules(self.check(rp1=bad)), ["R8"])

    def test_r9_changes_exist_and_differ(self):
        bad = copy.deepcopy(self.rp1)
        bad["entries"][2]["changes"] = ["C2.statement", "C1.statement"]
        self.assertEqual(rules(self.check(rp1=bad)), ["R9"])
        bad["entries"][2]["changes"] = []
        self.assertIn("R9", rules(self.check(rp1=bad)))

    def test_r10_rebuttal_needs_evidence(self):
        bad = copy.deepcopy(self.rp1)
        bad["entries"][1]["evidence"] = ["C3"]
        self.assertEqual(rules(self.check(rp1=bad)), ["R10"])
        bad["entries"][1]["evidence"] = ["RN4"]
        self.assertEqual(rules(self.check(rp1=bad)), ["R10"])    # no lab record given

    def test_r11_limitation_must_be_new_and_relevant(self):
        bad = copy.deepcopy(self.rp1)
        bad["entries"][0].update(limitation="L1", changes=[])
        self.assertIn("R11", rules(self.check(rp1=bad)))

    def test_r12_and_r13(self):
        bad = copy.deepcopy(self.rp1)
        bad["entries"][1] = {"comment": f"{V1}/RV1.c2", "response": "test_challenged",
                             "alternative_test": dict(TEST_RHO, withdrawn="")}
        self.assertIn("R12", rules(self.check(rp1=bad)))
        bad = copy.deepcopy(self.rp1)
        bad["entries"][1]["request_human"] = True
        self.assertEqual(rules(self.check(rp1=bad)), ["R13"])

    def test_r14_names_the_claimset(self):
        bad = copy.deepcopy(self.v2)
        bad["paper"]["id"] = "doi:10.0000/claimset.2026.000999"
        self.assertEqual(rules(self.check(v2=bad)), ["R14"])

    def test_r15_every_change_is_answered_or_explained(self):
        v2 = copy.deepcopy(self.v2)
        v2["paper"]["title"] += " (revised)"
        self.assertEqual(rules(self.check(v2=v2)), ["R15"])
        rp1 = copy.deepcopy(self.rp1)
        rp1["other_changes"] = [{"path": "paper.title", "reason": "Title names the revision."}]
        self.assertEqual(self.check(v2=v2, rp1=rp1).errors, [])

    def test_r16_removed_ids_are_not_reused(self):
        self.publish_v2()
        write(self.dir / "v2" / "reviews" / "RV2.json", self.re_review())
        v3 = copy.deepcopy(self.v2)
        v3["limitations"] = [l for l in v3["limitations"] if l["id"] != "L2"]
        v3["claims"][2]["limitations"] = ["L1"]
        (self.dir / "v3").mkdir()
        pub3 = copy.deepcopy(self.pub2)
        pub3["limitations"] = [l for l in pub3["limitations"] if l["id"] != "L2"]
        next(c for c in pub3["claims"] if c["id"] == "C2")["limitations"] = ["L1"]
        pub3["paper"].update(version=3, previous_version=f"{DOI}@v2", answers=f"{DOI}@v3/RP2")
        write(self.dir / "v3" / "claimset.json", pub3)
        rec = self.record()
        v4 = copy.deepcopy(self.v2)          # brings L2 back
        rp = {"schema": "claimset-response", "id": "RP3", "revises": {"version": f"{DOI}@v3", "hash": rec.current.hash},
              "responds_to": [], "entries": [], "other_changes": [{"path": "L2", "reason": "r"},
                                                                  {"path": "C2.limitations", "reason": "r"}]}
        rep, _ = rv.check_revision(rec, v4, rp)
        self.assertIn("R16", rules(rep))

    def test_r17_lab_record_prefix(self):
        lab1 = '{"id":"INIT","type":"init"}\n'
        (self.dir / "v1" / "lab.jsonl").write_text(lab1, encoding="utf-8")
        self.assertEqual(rules(self.check()), ["R17"])
        other = Path(self.tmp.name) / "lab.jsonl"
        other.write_text('{"id":"INIT","type":"init","x":1}\n', encoding="utf-8")
        self.assertIn("R17", rules(self.check(lab=other)))

    def test_w_r3_declined_reversal(self):
        rp1 = copy.deepcopy(self.rp1)
        rp1["entries"][1] = {"comment": f"{V1}/RV1.c2", "response": "declined"}
        rp1["other_changes"] = [{"path": p, "reason": "r"} for p in ("C3.check", "C3.check_evidence", "E2")]
        rep = self.check(rp1=rp1)
        self.assertEqual(rep.errors, [])
        self.assertEqual([w["rule"] for w in rep.warnings], ["W-R3"])


class TestDisputes(RecordCase):
    def test_w_r4_disputed_twice_without_a_human(self):
        self.publish_v2()
        test2 = dict(TEST_RHO, upheld="rho > 0.2", withdrawn="rho <= 0.2")
        rv2 = self.re_review()
        rv2["verdicts"][1] = {"comment": f"{V1}/RV1.c2", "state": "disputed", "reason": "E2 measures a subset.",
                              "replacement_test": test2}
        write(self.dir / "v2" / "reviews" / "RV2.json", rv2)
        rec = self.record()
        rp2 = {"schema": "claimset-response", "schema_version": "0.1", "id": "RP2",
               "revises": {"version": f"{DOI}@v2", "hash": rec.current.hash},
               "responds_to": [{"review": f"{DOI}@v2/RV2", "hash": rv.doc_hash(rv2)},
                               {"review": f"{V1}/RV1", "hash": rv.doc_hash(self.rv1)}],
               "entries": [{"comment": f"{V1}/RV1.c1", "response": "declined"},
                           {"comment": f"{V1}/RV1.c2", "response": "declined"}], "other_changes": []}
        rep, _ = rv.check_revision(rec, self.v2, rp2)
        self.assertEqual(rep.errors, [])
        pub3 = publish(self.dir, 3, self.v2, self.pub2, rp2)
        rv3 = dict(rv2, id="RV3", target={"version": f"{DOI}@v3", "hash": rv.doc_hash(pub3)},
                   verdicts=[{"comment": f"{V1}/RV1.c1", "state": "limited", "reason": "L2."},
                             {"comment": f"{V1}/RV1.c2", "state": "disputed", "reason": "Still a subset.",
                              "replacement_test": dict(TEST_RHO, upheld="rho > 0.1", withdrawn="rho <= 0.1")}])
        self.assertEqual(rv.check_review(self.record(), rv3).errors, [])
        write(self.dir / "v3" / "reviews" / "RV3.json", rv3)
        rec = self.record()
        self.assertEqual(rec.comments()[f"{V1}/RV1.c2"].test["upheld"], "rho > 0.1")    # the latest test applies
        rp3 = dict(rp2, id="RP3", revises={"version": f"{DOI}@v3", "hash": rec.current.hash},
                   responds_to=rp2["responds_to"] + [{"review": f"{DOI}@v3/RV3", "hash": rv.doc_hash(rv3)}])
        rep, _ = rv.check_revision(rec, self.v2, rp3)
        self.assertEqual(rep.errors, [])
        self.assertEqual(sorted(w["rule"] for w in rep.warnings), ["W-R3", "W-R4"])
        rp3["entries"][1] = {"comment": f"{V1}/RV1.c2", "response": "rebutted", "evidence": ["E2"],
                             "request_human": True}
        rep, _ = rv.check_revision(rec, self.v2, rp3)
        self.assertEqual(rep.errors, [])
        self.assertNotIn("W-R4", [w["rule"] for w in rep.warnings])


class TestRespondAndScore(RecordCase):
    def test_draft_response(self):
        response, todo = rv.respond(self.record(), self.v2)
        kinds = {e["comment"].rsplit(".", 1)[1]: e["response"] for e in response["entries"]}
        self.assertEqual(kinds, {"c1": "declined", "c2": "changed", "c3": "changed"})
        self.assertEqual(response["revises"]["hash"], rv.doc_hash(self.v1))
        self.assertTrue(any("L2" in t for t in todo))       # changes no entry covers need a reason

    def test_status(self):
        comments = self.record().comments()
        self.assertEqual({c.state for c in comments.values()}, {"open"})
        self.publish_v2()
        write(self.dir / "v2" / "reviews" / "RV2.json", self.re_review())
        states = {k.rsplit(".", 1)[1]: c.state for k, c in self.record().comments().items()}
        self.assertEqual(states, {"c1": "limited", "c2": "withdrawn", "c3": "resolved"})
        self.assertEqual(list(self.record().unresolved()), [f"{V1}/RV1.c1"])

    def test_latest_scores(self):
        self.assertEqual(self.record().scores()["C2"]["score"], 60)
        self.publish_v2()
        self.assertEqual(self.record().scores(), {})          # no score until the revision is reviewed
        write(self.dir / "v2" / "reviews" / "RV2.json", self.re_review())
        self.assertEqual({k: v["score"] for k, v in self.record().scores().items()}, {"C1": 100, "C3": 100, "C2": 90})
        self.assertEqual(self.record().scores(1)["C2"]["score"], 60)    # v1 keeps its scores

    def test_cli(self):
        out, err = io.StringIO(), io.StringIO()
        v2 = Path(self.tmp.name) / "v2.json"
        rp = Path(self.tmp.name) / "rp1.json"
        write(v2, self.v2)
        write(rp, self.rp1)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["revision", "status", str(self.dir)]), 0)
            self.assertEqual(cli.main(["revision", "check", str(self.dir), "--revision", str(v2),
                                       "--response", str(rp)]), 0)
            self.assertEqual(cli.main(["revision", "respond", str(self.dir), "--revision", str(v2)]), 0)
            self.assertEqual(cli.main(["validate", str(v2), "--revision"]), 0)
            self.assertEqual(cli.main(["revision", "status", str(self.dir / "missing")]), 1)
        self.assertIn("RV1.c2", out.getvalue())


class TestLabAnswersAReview(RecordCase):
    """A comment's resolution test run as a planned test (lab record §5.7.7), and the response drawn from it."""

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue().strip(), err.getvalue().strip()

    def setUp(self):
        super().setUp()
        self.old = {k: os.environ.get(k) for k in ("CLAIMSET_LAB", "CLAIMSET_ACTOR")}
        self.lab = Path(self.tmp.name) / "lab"
        os.environ["CLAIMSET_LAB"] = str(self.lab)
        os.environ.pop("CLAIMSET_ACTOR", None)
        self.review = Path(self.tmp.name) / "RV1.json"
        write(self.review, self.rv1)
        self.script = Path(self.tmp.name) / "rho.py"
        self.script.write_text("import json, os\njson.dump([{'quantity': 'largest absolute cross-series correlation "
                               "of forecast errors', 'value': 0.12}], open(os.environ['CLAIMSET_RESULTS'], 'w'))\n",
                               encoding="utf-8")

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_plan_from_comment_then_respond(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        self.run_cli("lab", "actor", "--kind", "agent", "--name", "A", "--model",
                     "Anthropic|Claude Opus|5.5|claude-opus-5-5", "--by", "AC1")
        self.run_cli("lab", "question", "Are M's errors independent across series?", "--by", "AC2")
        self.run_cli("lab", "hypothesis", "Method M's forecast errors are at most weakly correlated across series.",
                     "--question", "Q1", "--by", "AC2")
        cid = f"{V1}/RV1.c2"
        code, _, err = self.run_cli("lab", "plan", "--kind", "confirmatory", "--hypotheses", "H1", "--comment",
                                    cid, "--analysis", "largest absolute correlation", "--by", "AC2")
        self.assertEqual(code, 1)
        self.assertIn("--review", err)
        code, _, err = self.run_cli("lab", "plan", "--kind", "confirmatory", "--hypotheses", "H1", "--comment",
                                    f"{V1}/RV1.c1", "--review", str(self.review), "--analysis", "a", "--by", "AC2")
        self.assertEqual(code, 1)
        self.assertIn("decided by text", err)
        code, out, err = self.run_cli("lab", "plan", "--kind", "confirmatory", "--hypotheses", "H1", "--comment",
                                      cid, "--review", str(self.review), "--analysis",
                                      "largest absolute correlation", "--by", "AC2")
        self.assertEqual((code, out), (0, "PL1"), err)
        from claimset.lab.core import Lab
        body = Lab(self.lab).state().get("PL1")["body"]
        self.assertEqual(body["addresses"], [cid])
        self.assertEqual(body["decision"]["supported"], "rho <= 0.3")
        self.assertEqual(body["design"], TEST_RHO["procedure"])
        code, _, err = self.run_cli("lab", "run", "--plan", "PL1", "--by", "AC2", "--", sys.executable,
                                    str(self.script))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.run_cli("lab", "verdict", "PL1", "--json")[1])["verdict"], "supported")
        self.assertEqual(self.run_cli("lab", "status", "H1", "supported", "--plan", "PL1", "--evidence", "RS1",
                                      "--why", "Rule of PL1: rho = 0.12.", "--by", "AC2")[0], 0)
        from claimset.lab.export import export
        out = export(Lab(self.lab))
        claim = next(c for c in out["draft"]["claims"] if c.get("test"))
        self.assertEqual(claim["test"]["addresses"], [cid])
        response, todo = rv.respond(self.record(), self.v2, lab=self.lab / "lab.jsonl")
        entry = next(e for e in response["entries"] if e["comment"] == cid)
        self.assertEqual(entry["response"], "rebutted")
        self.assertIn("RN1", entry["evidence"])
        rp1 = copy.deepcopy(self.rp1)
        rp1["entries"][1]["evidence"] = ["E2", "RN1"]
        rep, _ = rv.check_revision(self.record(), self.v2, rp1, lab=self.lab / "lab.jsonl")
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])        # the planned test makes the rebuttal strong

    def test_addresses_must_be_comment_ids(self):
        self.run_cli("lab", "init", "--title", "Study")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        code, _, err = self.run_cli("lab", "plan", "--kind", "exploratory", "--design", "d", "--analysis", "a",
                                    "--addresses", "RV1.c2", "--by", "AC1")
        self.assertEqual(code, 1)
        self.assertIn("addresses", err)


if __name__ == "__main__":
    unittest.main()
