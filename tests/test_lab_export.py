"""Tests for exporting a lab record to a draft claimset (lab record spec v0.4 §9)."""

import json
import os
import sys
import unittest
from pathlib import Path

from claimset.lab import anchor as anchor_mod
from claimset.lab.core import LabError, canonical
from claimset.lab.export import export
from claimset.lab.run import run_command
from claimset.validate import validate

from test_lab import PlanRunCase, TestCli
from test_lab_protect import HAVE_GIT, make_repo

SCRIPT = """
import json, os, sys
json.dump([{"quantity": "mean relative MASE reduction", "value": float(sys.argv[1]),
            "conditions": {"frequency": sys.argv[2]}}], open(os.environ["CLAIMSET_RESULTS"], "w"))
sys.exit(int(sys.argv[3]) if len(sys.argv) > 3 else 0)
"""


class ExportCase(PlanRunCase):
    def setUp(self):
        super().setUp()
        self.script.write_text(SCRIPT)

    def run_(self, value, cond, plan=None, exploratory=False, by=None, code=0):
        out = run_command(self.lab, by or self.agent, [sys.executable, str(self.script), str(value), cond, str(code)],
                          plan=plan, exploratory=exploratory, echo=False, cwd=self.tmp.name)
        return out["run"]["id"], [r["id"] for r in out["results"]]

    def status(self, h, to, evidence=(), why="r", **extra):
        body = {"target": h, "to": to, "rationale": why}
        if evidence:
            body["evidence"] = [evidence] if isinstance(evidence, str) else list(evidence)
        body.update(extra)
        return self.add("status", body)

    def claim(self, out, lab_event):
        return next(c for c in out["draft"]["claims"] if c.get("hypothesis", {}).get("lab_event") == lab_event)

    def todo(self, out):
        return " | ".join(f"{t['where']}: {t['what']}" for t in out["completion"])


class TestPreconditions(ExportCase):
    def test_open_hypothesis_refused(self):
        with self.assertRaises(LabError) as cm:
            export(self.lab)
        self.assertIn(self.h, cm.exception.message)
        self.assertIn("abandoned", cm.exception.message)

    def test_unverifiable_record_refused(self):
        self.status(self.h, "abandoned")
        self.write_lines(self.lines()[:-2] + self.lines()[-1:])
        with self.assertRaises(LabError) as cm:
            export(self.lab)
        self.assertIn("does not verify", cm.exception.message)

    def test_unmerged_agent_log_refused(self):
        self.status(self.h, "abandoned")
        self.lab.start_agent(self.agent)
        self.add("note", {"text": "agent at work"})
        with self.assertRaises(LabError) as cm:
            export(self.lab)
        self.assertIn("merge", cm.exception.message)
        self.lab.merge(self.human)
        export(self.lab)


class TestMapping(ExportCase):
    def setUp(self):
        super().setUp()
        self.h2 = self.hyp("X improves Y weekly.")
        self.h3 = self.hyp("X improves Y daily.")
        self.pl = self.plan(hypotheses=[self.h, self.h2])
        self.rn1, self.rs1 = self.run_(0.08, "monthly", plan=self.pl)
        self.rn2, self.rs2 = self.run_(-0.004, "weekly", plan=self.pl)
        self.add("deviation", {"plan": self.pl, "run": self.rn2, "text": "Used 900 series.", "reason": "Data gap."})
        self.rn3, self.rs3 = self.run_(0.05, "daily")
        self.rn4, _ = self.run_(0.0, "failed", code=3)
        self.status(self.h, "supported", self.rs1)
        self.status(self.h2, "not_supported", self.rs2)
        self.status(self.h3, "inconclusive", [self.rn3])
        self.h4 = self.hyp("X improves Y hourly.")
        self.status(self.h4, "abandoned")
        self.h5 = self.hyp("X improves Y annually.")
        self.h6 = self.hyp("X improves Y annually, for long series.")
        self.status(self.h5, "revised", why="narrowed", revised_to=self.h6)
        self.status(self.h6, "abandoned")
        self.out = export(self.lab)

    def test_claims_for_tested_hypotheses_only(self):
        lab_events = [c["hypothesis"]["lab_event"] for c in self.out["draft"]["claims"] if "hypothesis" in c]
        self.assertEqual(lab_events, [self.h, self.h2, self.h3])
        self.assertEqual([c["hypothesis"]["outcome"] for c in self.out["draft"]["claims"]],
                         ["supported", "not_supported", "inconclusive"])

    def test_planning_labels(self):
        c1, c2, c3 = (self.claim(self.out, h) for h in (self.h, self.h2, self.h3))
        self.assertEqual(c1["planning"], "confirmatory")
        self.assertEqual(c1["planned_at"], self.lab.state().get(self.pl)["at"])
        self.assertEqual(c1["planned_at_anchor"], "none")
        self.assertEqual(c2["planning"], "confirmatory_with_deviations")
        self.assertEqual(c3["planning"], "exploratory")
        self.assertNotIn("planned_at", c3)

    def test_deviation_becomes_limitation(self):
        c2 = self.claim(self.out, self.h2)
        lim = next(l for l in self.out["draft"]["limitations"] if l["id"] in c2["limitations"])
        self.assertIn(self.rn2, lim["statement"])
        self.assertIn("Data gap.", lim["statement"])
        self.assertEqual(lim["affects"], [c2["id"]])

    def test_evidence_and_results(self):
        c1 = self.claim(self.out, self.h)
        ev = next(e for e in self.out["draft"]["evidence"] if e["id"] == c1["support"][0]["evidence"])
        self.assertEqual(ev["lab_runs"], [self.rn1])
        self.assertEqual(ev["results"][0]["value"], 0.08)
        self.assertEqual(ev["results"][0]["conditions"], {"frequency": "monthly"})
        self.assertEqual(c1["support"][0]["results"], [f"{ev['id']}.R1"])
        self.assertIn("software", ev["execution"])

    def test_exploration_counts(self):
        ex = self.out["draft"]["paper"]["exploration"]
        self.assertEqual(ex["hypotheses"], {"total": 6, "supported": 1, "not_supported": 1, "inconclusive": 1,
                                            "revised": 1, "abandoned": 2})
        self.assertEqual(ex["runs"], {"total": 4, "confirmatory": 2, "exploratory": 2, "failed": 1})
        self.assertEqual(ex["questions"], 1)
        lab_art = next(a for a in self.out["draft"]["artefacts"] if a["id"] == ex["lab_record"])
        self.assertEqual(lab_art["kind"], "lab_record")
        self.assertTrue(lab_art["version"].startswith("sha256:"))

    def test_contributors(self):
        contributors = {c["name"]: c for c in self.out["draft"]["contributors"]}
        agent = contributors["Agent"]
        self.assertEqual(agent["positions"], ["author"])
        self.assertEqual(agent["tools"], ["code_execution"])
        self.assertEqual(agent["models"], [{"model": "M1", "role": "sole"}])
        human = contributors["A. Researcher"]
        self.assertEqual(human["positions"], ["contributor"])
        self.assertEqual(human["orcid"], "0000-0000-0000-0000")
        self.assertEqual(self.out["draft"]["models"][0]["accessed"], agent["run_period"]["start"])

    def test_completion_list_names_decisions(self):
        text = self.todo(self.out)
        for needle in ("paper.licence", "paper.contributions", "paper.autonomy", "negative result", "guarantor",
                       "profile"):
            self.assertIn(needle, text)
        self.assertNotIn("instruction", json.dumps(self.out["draft"]).lower())

    def test_completed_draft_validates(self):
        draft = self.out["draft"]
        draft["paper"]["licence"] = "CC-BY-4.0"
        human = next(c for c in draft["contributors"] if c["type"] == "human")
        human["positions"] = ["supervisor", "guarantor"]
        c1 = self.claim(self.out, self.h)
        c1["role"] = "principal"
        draft["paper"]["contributions"] = [c1["id"]]
        rep = validate(draft)
        self.assertEqual(rep.errors, [])


class TestDetails(ExportCase):
    def test_plan_corrected_after_run_uses_version_before_run(self):
        pl = self.plan()
        rn, rs = self.run_(0.1, "m", plan=pl)
        self.add("plan", {"kind": "confirmatory", "hypotheses": [self.h], "design": "changed later", "analysis": "a",
                          "criterion": "c"}, supersedes=pl)
        self.status(self.h, "supported", rs)
        c = export(self.lab)["draft"]["claims"][0]
        self.assertEqual(c["planned_at"], self.lab.state().get(pl)["at"])

    def test_confirmatory_run_of_plan_not_naming_hypothesis_is_exploratory(self):
        other = self.hyp("Something else.")
        pl = self.plan(hypotheses=[other])
        rn, rs = self.run_(0.1, "m", plan=pl)
        self.status(self.h, "supported", rs)
        self.status(other, "abandoned")
        self.assertEqual(self.claim(export(self.lab), self.h)["planning"], "exploratory")

    def test_checks_follow_their_targets(self):
        checker = self.lab.append("actor", {"kind": "agent", "name": "Checker",
                                            "models": [{"provider": "Q", "name": "N", "version": "2"}]},
                                  by=self.human)["id"]
        rn, rs = self.run_(0.1, "m")
        st = self.status(self.h, "supported", rs)
        self.add("check", {"target": rn, "outcome": "confirmed", "extent": "full"}, by=checker)
        self.add("check", {"target": st, "outcome": "disputed", "extent": "partial", "notes": "bad"}, by=self.human)
        self.add("check", {"target": self.q, "outcome": "confirmed", "extent": "full"}, by=checker)
        out = export(self.lab)
        c = self.claim(out, self.h)
        self.assertEqual([x["outcome"] for x in c["checks"]], ["confirmed", "disputed"])
        checker_p = next(p for p in out["draft"]["contributors"] if p["name"] == "Checker")
        self.assertEqual(checker_p["positions"], ["contributor"])
        self.assertEqual(checker_p["ai_tasks"], [{"task": "review", "extent": "full"}])
        self.assertIn(f"targets {self.q}", self.todo(out))

    def test_assumption_with_check_evidence(self):
        rn, rs = self.run_(0.1, "m")
        self.status(self.h, "supported", rs)
        self.add("assumption", {"statement": "Errors are independent.", "category": "statistical",
                                "check": "tested", "check_evidence": rs[0]})
        out = export(self.lab)
        a = next(c for c in out["draft"]["claims"] if c.get("assumption_category"))
        ev = self.claim(out, self.h)["support"][0]["evidence"]
        self.assertEqual(a["check_evidence"], ev)
        self.assertEqual(a["support"], [{"kind": "assumption"}])

    def test_status_without_runs_has_no_support(self):
        nt = self.add("note", {"text": "observed"})
        self.status(self.h, "inconclusive", [nt])
        out = export(self.lab)
        self.assertEqual(self.claim(out, self.h)["support"], [])
        self.assertIn("no run lies behind", self.todo(out))

    def test_withheld_events_listed(self):
        nt = self.add("note", {"text": "names the data supplier"})
        self.status(self.h, "abandoned")
        lines = self.lines()
        events = [json.loads(l) for l in lines]
        for e in events:
            if e["id"] == nt:
                e["body"] = {"withheld": True, "reason": "commercial", "comment": "Supplier name."}
        self.write_lines([canonical(e).decode("utf-8") for e in events])
        lab_art = export(self.lab)["draft"]["artefacts"][-1]
        self.assertEqual(lab_art["withheld_events"], [{"event": nt, "reason": "commercial", "comment": "Supplier name."}])

    def test_merged_agent_work_exports_with_prefixed_ids(self):
        self.lab.start_agent(self.agent)
        h = self.add("hypothesis", {"statement": "Agent hypothesis.", "question": self.q, "origin": "a_priori"})
        rn, rs = self.run_(0.2, "m")
        self.status(h, "supported", rs)
        self.status(self.h, "abandoned")
        self.lab.merge(self.human)
        out = export(self.lab)
        c = self.claim(out, h)
        self.assertEqual(c["hypothesis"]["lab_event"], "AC2.H1")
        ev = out["draft"]["evidence"][0]
        self.assertEqual(ev["lab_runs"], ["AC2.RN1"])
        self.assertFalse([e for e in validate(out["draft"]).errors if e["rule"] == "1"])


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TestAnchoredExport(ExportCase):
    def test_anchor_strength_recorded(self):
        make_repo(self.tmp.name)
        anchor_mod.save_config(self.lab, {"anchor": {"service": "git_remote", "remote": "origin",
                                                     "branch": "claimset-lab"}})
        pl = self.plan()
        rn, rs = self.run_(0.1, "m", plan=pl)
        self.status(self.h, "supported", rs)
        out = export(self.lab)
        self.assertEqual(self.claim(out, self.h)["planned_at_anchor"], "git_remote")
        lab_art = out["draft"]["artefacts"][-1]
        self.assertEqual(lab_art["uri"], "lab/lab.jsonl")


class TestExportCli(TestCli):
    def test_export_writes_draft_and_lists_completion(self):
        self.run_cli("lab", "init", "--title", "Study", "--repository", "https://example.org/repo")
        self.run_cli("lab", "actor", "--kind", "human", "--name", "R")
        os.environ["CLAIMSET_ACTOR"] = "AC1"
        self.run_cli("lab", "question", "Q?")
        self.run_cli("lab", "hypothesis", "H.", "--question", "Q1")
        self.run_cli("lab", "status", "H1", "abandoned", "--why", "out of scope")
        out_file = Path(self.tmp.name) / "draft.json"
        code, out, err = self.run_cli("lab", "export", "-o", str(out_file))
        self.assertEqual(code, 0, err)
        self.assertIn("To complete before submission:", out)
        draft = json.loads(out_file.read_text())
        self.assertEqual(draft["paper"]["title"], "Study")
        self.assertEqual(draft["artefacts"][-1]["uri"], "https://example.org/repo/lab/lab.jsonl")
        code, _, err = self.run_cli("lab", "export", "-o", str(out_file))
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        code, out, _ = self.run_cli("lab", "export", "-o", str(out_file), "--force", "--json")
        self.assertEqual(json.loads(out)["validation"]["errors"][0]["rule"], "19")


if __name__ == "__main__":
    unittest.main()
