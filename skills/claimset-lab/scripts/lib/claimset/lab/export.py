"""Export a lab record to a draft claimset submission (lab record spec §9).

The draft holds only what the record states. Decisions the record cannot make
(licence, guarantor, principal claims, claim types, wording of negative
results...) are left at neutral defaults or left out, and listed in a separate
completion list: a claimset never contains instructions.
"""

import subprocess
from pathlib import Path

from .. import SPEC_VERSION
from .anchor import covers, read_anchors
from . import decision as _decision
from .core import LabError, State, agent_base, file_hash, final_statuses, is_withheld, plan_flags, verify

FINAL = {"supported", "not_supported", "inconclusive", "revised", "abandoned"}
OUTCOMES = ("supported", "not_supported", "inconclusive")
ARTEFACT_KINDS = {"code", "data", "protocol", "proof", "model", "software", "figure", "table", "prompt",
                  "trace", "manuscript", "other"}


def _repo_path(path):
    """`path` relative to the top of its git repository, or None."""
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=Path(path).parent,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0:
        return None
    try:
        return Path(path).resolve().relative_to(Path(top.stdout.strip()).resolve()).as_posix()
    except ValueError:
        return None


class _Exporter:
    def __init__(self, lab):
        self.lab = lab
        rep = verify(lab)
        if not rep["ok"]:
            first = rep["errors"][0]
            raise LabError("export", f"the record does not verify ({len(rep['errors'])} error(s); first: "
                                     f"[{first['rule']}] {first['message']}); run `claimset lab verify`")
        self.events = lab.read()
        st = self.st = State()
        for e in self.events:
            st.apply(e)
        unmerged = [a for a in lab.agent_logs() if a not in st.merged and lab.read_agent(a)]
        if unmerged:
            raise LabError("export", f"agent logs of {', '.join(unmerged)} are not merged; run `claimset lab merge`")
        self.roots = [e for e in st.events if e["id"] == st.root(e["id"])]
        self.hyps = [e for e in self.roots if e["type"] == "hypothesis"]
        self.statuses = final_statuses(st)
        # Verdicts: the latest status naming each (hypothesis, plan), corrections in their original's place.
        self.verdicts = {}
        for e in st.events:
            b = e.get("body") if isinstance(e.get("body"), dict) else {}
            if e["type"] != "status" or is_withheld(e) or e["id"] in st.superseded_by \
                    or not isinstance(b.get("plan"), str) or not isinstance(b.get("target"), str):
                continue
            key = (st.root(b["target"]), st.root(b["plan"]))
            place = st.pos[st.root(e["id"])]
            if key not in self.verdicts or place > self.verdicts[key][0]:
                self.verdicts[key] = (place, e)
        self.verdicts = {k: v[1] for k, v in self.verdicts.items()}
        missing = [(h["id"], root) for h in (self.hyps if st.strict else [])
                   for root in st.decided_plans_for(h["id"]) if (h["id"], root) not in self.verdicts]
        if missing and not st.has_withheld:
            h, root = missing[0]
            raise LabError("export", f"the planned test of {h} under {root} has run but its verdict is not recorded; "
                                     f"see `claimset lab verdict {root}` and record it with "
                                     f"`claimset lab status {h} … --plan {root}`. Every planned test that ran is "
                                     f"reported")
        self.anchors = read_anchors(lab)
        # Where each merged agent log started in the main log (§3.1), to know what its agent saw.
        self.bases = {}
        for a in st.merged:
            try:
                agent = lab.read_agent(a)
            except LabError:
                continue
            if agent:
                self.bases[a] = agent_base(self.events, agent)
        self.completion, self.mapping = [], {}
        self.contributors, self.models, self.claims, self.evidence = [], [], [], []
        self.limitations, self.artefacts = [], []
        self.person, self.evidence_of_run = {}, {}
        self.results_of = {}
        for e in st.events:
            if e["type"] == "result" and not is_withheld(e):
                self.results_of.setdefault(e["body"].get("run"), []).append(e)

    # -- helpers
    def todo(self, where, what):
        self.completion.append({"where": where, "what": what})

    def versions(self, eid):
        st = self.st
        cur = st.root(eid)
        out = [cur]
        while cur in st.superseded_by:
            cur = st.superseded_by[cur]
            out.append(cur)
        return out

    def body(self, eid):
        e = self.st.latest(eid)
        return {} if e is None or is_withheld(e) else e["body"]

    def actor_of(self, by):
        return self.st.root(by) if by else None

    def visible(self, v, run):
        """Whether event v was visible to whoever wrote `run` when it was written."""
        o = run.get("origin")
        if not isinstance(o, dict):
            return v["seq"] < run["seq"]
        agent = run["id"].split(".")[0]
        base = self.bases.get(agent)
        if base is not None and v["seq"] <= base:
            return True
        vo = v.get("origin")
        return isinstance(vo, dict) and v["id"].startswith(agent + ".") and vo.get("seq", 0) < o.get("seq", 0)

    def version_before(self, eid, run):
        """The latest version of eid's chain that the run's writer could see (not withheld), or None."""
        named = run["body"].get("plan_version") if isinstance(run.get("body"), dict) else None
        if isinstance(named, str) and self.st.get(named) is not None \
                and self.st.root(named) == self.st.root(eid):
            e = self.st.get(named)
            return None if is_withheld(e) else e
        found = None
        for v in self.versions(eid):
            e = self.st.get(v)
            if self.visible(e, run) and not is_withheld(e):
                found = e
        return found

    # -- parts of the draft
    def export_contributors(self):
        st = self.st
        writes, active = {}, {}
        for e in st.events:
            a = self.actor_of(e["by"])
            if a:
                writes.setdefault(a, set()).add(e["type"])
                first, last = active.get(a, (e["at"], e["at"]))
                active[a] = (min(first, e["at"]), max(last, e["at"]))
        self.writes = writes
        model_ids = {}
        self.withheld_actors = []
        for a in (e for e in self.roots if e["type"] == "actor"):
            cur = st.latest(a["id"])
            if is_withheld(cur):
                self.withheld_actors.append(a["id"])
                self.todo("contributors", f"actor {a['id']} is withheld in the record and is not exported; add it as "
                                          f"a contributor if it should be listed")
                continue
            b = cur["body"]
            pid = f"P{len(self.contributors) + 1}"
            self.person[a["id"]] = pid
            self.mapping[pid] = cur["id"]
            kind = b.get("kind")
            c = {"id": pid, "type": kind, "name": b.get("name")}
            if kind == "agent":
                authored = writes.get(a["id"], set()) - {"check", "actor"}
                c["positions"] = ["author"] if authored else ["contributor"]
                c["models"] = []
                period = active.get(a["id"])
                for m in b.get("models") or []:
                    key = (m.get("provider"), m.get("name"), m.get("version"), m.get("identifier"))
                    if key not in model_ids:
                        model_ids[key] = f"M{len(self.models) + 1}"
                        self.models.append({k: v for k, v in (("id", model_ids[key]), ("provider", key[0]),
                                                              ("name", key[1]), ("version", key[2]),
                                                              ("identifier", key[3])) if v})
                    mdef = next(x for x in self.models if x["id"] == model_ids[key])
                    if period and (mdef.get("accessed") is None or period[0][:10] < mdef["accessed"]):
                        mdef["accessed"] = period[0][:10]
                    c["models"].append({"model": model_ids[key]})
                if len(c["models"]) == 1:
                    c["models"][0]["role"] = "sole"
                if period:
                    c["run_period"] = {"start": period[0][:10], "end": period[1][:10]}
                if "run" in writes.get(a["id"], set()):
                    c["tools"] = ["code_execution"]
                if not authored and "check" in writes.get(a["id"], set()):
                    c["ai_tasks"] = [{"task": "review", "extent": "full"}]
                if b.get("record_source"):
                    c["record_source"] = b["record_source"]
            else:
                c["positions"] = ["contributor"]
                if b.get("orcid"):
                    c["orcid"] = b["orcid"]
                self.todo(f"contributors.{pid}", f"{c['name']} ({a['id']}) is exported as a contributor; ask whether "
                                                 f"they supervised the work and whether they are the guarantor")
            self.contributors.append(c)

    def make_evidence(self, run_ids):
        """One evidence object for the given runs. Returns (evidence ID, {result event ID: "E<n>.R<m>"})."""
        st = self.st
        runs = sorted((st.get(r) for r in set(run_ids)), key=lambda e: e["seq"])
        eid = f"E{len(self.evidence) + 1}"
        self.mapping[eid] = [r["id"] for r in runs]
        results, refs = [], {}
        for r in runs:
            self.evidence_of_run.setdefault(r["id"], eid)
            for res in self.results_of.get(r["id"], []):
                rid = f"R{len(results) + 1}"
                refs[res["id"]] = f"{eid}.{rid}"
                entry = {"id": rid}
                entry.update({k: res["body"][k] for k in ("quantity", "value", "unit", "interval", "conditions")
                              if res["body"].get(k) is not None})
                results.append(entry)
        visible = [r for r in runs if not is_withheld(r)]
        plans = []
        for r in visible:
            pv = self.version_before(r["body"]["plan"], r) if r["body"].get("plan") else None
            if pv and pv["id"] not in [p["id"] for p in plans]:
                plans.append(pv)
        parts = [f"Lab runs {', '.join(r['id'] for r in runs)}"
                 + (f" under plan {', '.join(p['id'] for p in plans)}." if plans else ".")]
        parts += [f"Design: {p['body'].get('design')} Analysis: {p['body'].get('analysis')}" for p in plans]
        if not plans:
            parts += [f"Command: {' '.join(r['body'].get('command') or [])}" for r in visible]
        ev = {"id": eid, "profile": "quant", "design": "other", "description": " ".join(parts),
              "availability": {"level": "public"}, "lab_runs": [r["id"] for r in runs]}
        if results:
            ev["results"] = results
        execution = {}
        commits = {r["body"].get("code_version") for r in visible}
        if len(commits) == 1 and None not in commits and len(visible) == len(runs):
            execution["code_version"] = commits.pop()
        software = [(r["body"].get("environment") or {}).get("software") for r in visible]
        if software and software[0] and all(s == software[0] for s in software) and len(visible) == len(runs):
            execution["software"] = software[0]
        dirty = [r["id"] for r in visible if r["body"].get("dirty")]
        if dirty:
            execution["notes"] = (f"Runs {', '.join(dirty)} ran with uncommitted changes in the working tree, "
                                  f"so their code version is not exactly the recorded commit.")
        if execution:
            ev["execution"] = execution
        self.evidence.append(ev)
        self.todo(f"evidence.{eid}", f"choose the profile and design of {eid} (exported as quant, design other) and "
                                     f"make its description self-contained")
        return eid, refs

    def hypothesis_at(self, hypothesis_id, pos):
        """The latest version of a hypothesis written before position `pos` (not withheld), or None."""
        found = None
        for v in self.versions(hypothesis_id):
            e = self.st.get(v)
            if self.st.pos[v] < pos and not is_withheld(e):
                found = e
        return found

    def claim_from(self, version, status, outcome, extra_runs=()):
        """A claim for a hypothesis version, with the evidence its status cites. Returns (claim, cited runs)."""
        st = self.st
        cid = f"C{len(self.claims) + 1}"
        self.mapping[cid] = version["id"]
        claim = {"id": cid, "type": "descriptive", "role": "supporting", "novelty": "new", "support": [],
                 "provenance": "author_stated", "hypothesis": {"lab_event": version["id"], "outcome": outcome}}
        if is_withheld(version):
            self.todo(f"claims.{cid}.statement", f"hypothesis {version['id']} is withheld; write the statement of {cid}")
        else:
            claim = dict({"id": cid, "statement": version["body"].get("statement")}, **{k: v for k, v in claim.items()
                                                                                       if k != "id"})
        cited_runs, cited_results, other = [], [], []
        for ref in list(status["body"].get("evidence") or []):
            ev = st.get(ref)
            if ev is not None and ev["type"] == "run":
                if ev["id"] not in cited_runs:
                    cited_runs.append(ev["id"])
            elif ev is not None and ev["type"] == "result" and not is_withheld(ev):
                cited_results.append(ev["id"])
                if ev["body"].get("run") not in cited_runs:
                    cited_runs.append(ev["body"]["run"])
            else:
                other.append(ref + (" (withheld)" if ev is not None and is_withheld(ev) else ""))
        for r in extra_runs:
            if r not in cited_runs:
                cited_runs.append(r)
        if other:
            self.todo(f"claims.{cid}.support", f"status {status['id']} also cites {', '.join(other)}, which are not "
                                               f"exported as evidence")
        if cited_runs:
            eid, refs = self.make_evidence(cited_runs)
            support = {"kind": "empirical_quant", "evidence": eid}
            cited = [refs[x] for x in cited_results if x in refs]
            if cited or refs:
                support["results"] = cited or list(refs.values())
            claim["support"].append(support)
        else:
            self.todo(f"claims.{cid}.support", f"no run lies behind status {status['id']}; give {cid} its support")
        claim["_targets"] = set(self.versions(version["id"])) | set(self.versions(status["id"])) | set(cited_runs) | {
            x["id"] for r in cited_runs for x in self.results_of.get(r, [])}
        if outcome == "not_supported":
            self.todo(f"claims.{cid}.statement", f"{cid} is a negative result: rewrite the statement of "
                                                 f"{version['id']} to say plainly what was not found, and under what "
                                                 f"conditions")
        self.todo(f"claims.{cid}", f"confirm the type (exported as descriptive), role and novelty of {cid}, and "
                                   f"give its scope")
        return claim, cited_runs

    def add_deviation_limitation(self, claim, run, plan_version, dv):
        lid = f"L{len(self.limitations) + 1}"
        self.limitations.append({
            "id": lid, "category": "analytic_flexibility",
            "statement": f"Lab run {run['id']} deviated from plan {plan_version['id']}: {dv['body'].get('text')} "
                         f"Reason: {dv['body'].get('reason')}",
            "affects": [claim["id"]], "impact": "could_bias_magnitude", "mitigation": "none"})
        claim.setdefault("limitations", []).append(lid)
        self.todo(f"limitations.{lid}", f"review the category, impact and wording of {lid}, exported from "
                                        f"deviation {dv['id']}")

    def export_hypotheses(self):
        st = self.st
        deviations, self.withheld_deviations = {}, []
        for e in self.roots:
            if e["type"] != "deviation":
                continue
            cur = st.latest(e["id"])
            if is_withheld(cur):
                self.withheld_deviations.append(cur["id"])
            elif cur["body"].get("plan") and cur["body"].get("run"):
                deviations[(st.root(cur["body"]["plan"]), cur["body"]["run"])] = cur
        self.test_counts = {o: 0 for o in OUTCOMES}
        for h in self.hyps:
            decided = st.decided_plans_for(h["id"]) if st.strict else {}
            for root, (pv, runs) in sorted(decided.items(), key=lambda kv: st.pos[kv[1][1][0]["id"]]):
                verdict = self.verdicts.get((h["id"], root))
                if verdict is None:
                    self.todo("claims", f"the verdict of {root} on {h['id']} is not readable (withheld); add its claim")
                    continue
                outcome = verdict["body"]["to"]
                self.test_counts[outcome] += 1
                version = self.hypothesis_at(h["id"], st.pos[pv["id"]]) or st.latest(h["id"])
                claim, _ = self.claim_from(version, verdict, outcome, [r["id"] for r in runs])
                devs = [(r, deviations[(root, r["id"])]) for r in runs if (root, r["id"]) in deviations]
                claim["planning"] = "confirmatory_with_deviations" if devs else "confirmatory"
                claim["planned_at"] = pv["at"]
                started = runs[0]["body"].get("started_at") or runs[0]["at"]
                usable = sorted((a for a in self.anchors if isinstance(a, dict) and covers(a, pv)
                                 and isinstance(a.get("at"), str) and a["at"] <= started), key=lambda a: a["at"])
                claim["planned_at_anchor"] = usable[0].get("service", "none") if usable else "none"
                rule = pv["body"].get("decision") or {}
                claim["test"] = {k: rule[k] for k in ("framework", "parameters", "quantities", "supported",
                                                      "not_supported") if rule.get(k) is not None}
                claim["test"].update({"verdict": "computed" if _decision.computed(rule) else "judged",
                                      "runs": pv["body"].get("runs", 1), "lab_plan": pv["id"]})
                flags = plan_flags(st, pv, runs)
                if flags:
                    claim["test"]["flags"] = flags
                for r, dv in devs:
                    self.add_deviation_limitation(claim, r, pv, dv)
                self.claims.append(claim)
            if decided:
                continue
            status = self.statuses.get(h["id"])
            if status is None or status["body"].get("to") not in OUTCOMES:
                continue
            claim, cited_runs = self.claim_from(st.latest(h["id"]), status, status["body"]["to"])
            claim["planning"] = "exploratory"
            if not st.strict:
                self.legacy_planning(claim, h, cited_runs, deviations)
            self.claims.append(claim)
        if self.withheld_deviations:
            self.todo("claims[].planning", f"deviations {', '.join(self.withheld_deviations)} are withheld, so the "
                                           f"planning labels do not take them into account; check them")

    def legacy_planning(self, claim, h, cited_runs, deviations):
        """Planning labels for records begun under v0.4: a confirmatory run in the status's evidence under a plan
        naming the hypothesis."""
        st = self.st
        hyp_ids = set(self.versions(h["id"]))
        tests = []
        for r in sorted((st.get(x) for x in cited_runs), key=lambda e: e["seq"]):
            if is_withheld(r) or r["body"].get("label") != "confirmatory" or not r["body"].get("plan"):
                continue
            pv = self.version_before(r["body"]["plan"], r)
            if pv and hyp_ids & set(pv["body"].get("hypotheses") or []):
                tests.append((r, pv, deviations.get((st.root(pv["id"]), r["id"]))))
        clean = [t for t in tests if t[2] is None]
        chosen = clean[0] if clean else (tests[0] if tests else None)
        if not chosen:
            return
        claim["planning"] = "confirmatory" if clean else "confirmatory_with_deviations"
        r, pv, _ = chosen
        claim["planned_at"] = pv["at"]
        self.todo(f"claims.{claim['id']}.test", f"plan {pv['id']} has no decision rule (it was written under lab "
                                                f"record v0.4 with the criterion: {pv['body'].get('criterion')}); "
                                                f"state the rule in {claim['id']}.test as it was fixed before the run")
        started = r["body"].get("started_at") or r["at"]
        usable = sorted((a for a in self.anchors if isinstance(a, dict) and covers(a, pv)
                         and isinstance(a.get("at"), str) and a["at"] <= started), key=lambda a: a["at"])
        claim["planned_at_anchor"] = usable[0].get("service", "none") if usable else "none"
        for r, pv, dv in tests:
            if dv is not None:
                self.add_deviation_limitation(claim, r, pv, dv)

    def export_checks(self):
        st = self.st
        for e in self.roots:
            if e["type"] != "check":
                continue
            cur = st.latest(e["id"])
            if is_withheld(cur):
                self.todo("claims[].checks", f"check {cur['id']} is withheld and is not exported")
                continue
            b = cur["body"]
            target = b.get("target")
            matched = [c for c in self.claims if target in c.get("_targets", set())]
            if not matched:
                self.todo("claims[].checks", f"check {cur['id']} targets {target}, which no exported claim rests on; "
                                             f"it is not exported")
                continue
            by = self.person.get(self.actor_of(cur["by"]))
            checker = next((x for x in self.contributors if x["id"] == by), None)
            if checker is None or "author" in checker["positions"]:
                self.todo("claims[].checks", f"check {cur['id']} was made by {cur['by']}, who is an author or not "
                                             f"exported; a claimset check comes from a non-author, so it is not "
                                             f"exported")
                continue
            for c in matched:
                entry = {"by": by, "outcome": b.get("outcome"), "extent": b.get("extent")}
                entry.update({k: b[k] for k in ("notes", "resolution") if b.get(k)})
                c.setdefault("checks", []).append(entry)

    def export_assumptions(self):
        st = self.st
        for a in self.roots:
            if a["type"] != "assumption":
                continue
            cid = f"C{len(self.claims) + 1}"
            cur = st.latest(a["id"])
            self.mapping[cid] = cur["id"]
            if is_withheld(cur):
                self.claims.append({"id": cid, "type": "descriptive", "role": "supporting", "novelty": "new",
                                    "support": [{"kind": "assumption"}], "provenance": "author_stated"})
                self.todo(f"claims.{cid}", f"assumption {cur['id']} is withheld; write the statement, category and "
                                           f"check of {cid}")
                continue
            b = cur["body"]
            claim = {"id": cid, "statement": b.get("statement"), "type": "descriptive", "role": "supporting",
                     "novelty": "new", "support": [{"kind": "assumption"}],
                     "assumption_category": b.get("category"), "check": b.get("check"),
                     "provenance": "author_stated"}
            ce = b.get("check_evidence")
            if ce:
                ce_event = st.get(ce)
                run_id = None
                if ce_event is not None and ce_event["type"] == "run":
                    run_id = ce
                elif ce_event is not None and ce_event["type"] == "result" and not is_withheld(ce_event):
                    run_id = ce_event["body"].get("run")
                if run_id is None:
                    self.todo(f"claims.{cid}.check_evidence", f"assumption {cur['id']} was checked by {ce}, which is "
                                                              f"not a run or result; give {cid} its check_evidence")
                else:
                    if run_id not in self.evidence_of_run:
                        self.make_evidence([run_id])
                    claim["check_evidence"] = self.evidence_of_run[run_id]
            self.claims.append(claim)
        if any("assumption_category" in c for c in self.claims) or any(
                self.mapping.get(c["id"], "").startswith("AS") for c in self.claims):
            self.todo("claims[].assumptions", "link each claim to the assumption claims it relies on")

    def export_artefacts(self):
        st = self.st
        for a in self.roots:
            if a["type"] != "artefact":
                continue
            cur = st.latest(a["id"])
            if is_withheld(cur):
                self.todo("artefacts", f"artefact {cur['id']} is withheld and is not exported; add it if it should be "
                                       f"listed")
                continue
            b = cur["body"]
            aid = f"A{len(self.artefacts) + 1}"
            self.mapping[aid] = cur["id"]
            art = {"id": aid, "kind": b.get("kind") if b.get("kind") in ARTEFACT_KINDS else "other",
                   "uri": b.get("path_or_uri"), "availability": {"level": "public"}}
            art.update({k: v for k, v in (("version", b.get("hash")), ("licence", b.get("licence")),
                                          ("description", b.get("description"))) if v})
            if art["kind"] in ("figure", "table") and "description" not in art:
                self.todo(f"artefacts.{aid}.description", f"describe what {aid} shows, self-contained")
            self.artefacts.append(art)
        lab_aid = f"A{len(self.artefacts) + 1}"
        init = self.body("INIT")
        rel = _repo_path(self.lab.log_path) or f"{self.lab.root.name}/{self.lab.log_path.name}"
        repo = init.get("repository")
        art = {"id": lab_aid, "kind": "lab_record", "uri": f"{repo.rstrip('/')}/{rel}" if repo else rel,
               "version": file_hash(self.lab.log_path), "availability": {"level": "public"},
               "description": "Lab record of the study: questions, hypotheses, plans, runs, results and decisions, "
                              "as an append-only, hash-chained event log (Claimset lab record specification v0.5)."}
        withheld = [{"event": e["id"], "reason": e["body"].get("reason"), "comment": e["body"].get("comment")}
                    for e in self.st.events if is_withheld(e)]
        if withheld:
            art["withheld_events"] = [{k: v for k, v in w.items() if v is not None} for w in withheld]
        self.artefacts.append(art)
        self.mapping[lab_aid] = "lab.jsonl"
        if not repo:
            self.todo(f"artefacts.{lab_aid}.uri", "give the public URI of the lab record (the record names no "
                                                  "repository)")
        for x in self.artefacts:
            if "licence" not in x:
                self.todo(f"artefacts.{x['id']}.licence", f"give the licence of {x['id']}")
        return lab_aid

    def export_paper(self, lab_aid):
        counts = {k: 0 for k in ("supported", "not_supported", "inconclusive", "revised", "abandoned", "open")}
        for h in self.hyps:
            final = self.statuses.get(h["id"])
            to = final["body"].get("to") if final else None
            counts[to if to in FINAL else "open"] += 1
        runs = [e for e in self.st.events if e["type"] == "run"]
        visible = [r for r in runs if not is_withheld(r)]
        confirmatory = sum(r["body"].get("label") == "confirmatory" for r in visible)
        if len(visible) < len(runs):
            self.todo("paper.exploration.runs", f"{len(runs) - len(visible)} runs are withheld; they are counted as "
                                                f"exploratory and not failed; correct the counts if needed")
        exploration = {
            "lab_record": lab_aid,
            "questions": sum(1 for e in self.roots if e["type"] == "question"),
            "hypotheses": dict({"total": sum(counts.values())}, **counts),
            "tests": dict({"total": sum(self.test_counts.values())}, **self.test_counts),
            "runs": {"total": len(runs), "confirmatory": confirmatory, "exploratory": len(runs) - confirmatory,
                     "failed": sum(r["body"].get("exit_status") != "success" for r in visible)},
        }
        paper = {"contributions": [], "autonomy": "unknown",
                 "contribution_record": "partial" if self.withheld_actors else "complete",
                 "origin": "native", "exploration": exploration}
        title = self.body("INIT").get("working_title")
        if title:
            paper = dict({"title": title}, **paper)
        else:
            self.todo("paper.title", "give the title (the record's working title is withheld)")
        self.completion[:0] = [
            {"where": "paper.licence", "what": "ask the supervisor or guarantor which licence applies "
                                               "(SPDX identifier)"},
            {"where": "contributors", "what": "ask who the guarantor is (a person with an ORCID, or an organisation) "
                                              "and give them the guarantor position"},
            {"where": "paper.contributions", "what": "choose the principal claims, set their role to principal and "
                                                     "list them in priority order"},
            {"where": "paper.autonomy", "what": "set autonomy (exported as unknown): autonomous, human_supervised or "
                                                "human_assisted"},
            {"where": "paper.classification.keywords", "what": "propose keywords"},
            {"where": "paper.contribution_record", "what": "confirm that every contributor is listed (exported from "
                                                           "everyone who wrote to the lab record)"},
        ]
        return paper

    def run(self):
        self.export_contributors()
        self.export_hypotheses()
        self.export_checks()
        self.export_assumptions()
        for c in self.claims:
            c.pop("_targets", None)
        lab_aid = self.export_artefacts()
        paper = self.export_paper(lab_aid)
        draft = {"schema_version": SPEC_VERSION, "paper": paper, "contributors": self.contributors,
                 "models": self.models, "definitions": [], "claims": self.claims, "evidence": self.evidence,
                 "warrants": [], "limitations": self.limitations, "artefacts": self.artefacts}
        return {"draft": draft, "completion": self.completion, "mapping": self.mapping}


def export(lab):
    """Return {"draft", "completion", "mapping"}. Raises LabError if a precondition of §9 fails."""
    return _Exporter(lab).run()
