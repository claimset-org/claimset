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
from .core import LabError, State, file_hash, is_withheld, verify

FINAL = {"supported", "not_supported", "inconclusive", "revised", "abandoned"}
OUTCOMES = ("supported", "not_supported", "inconclusive")
ARTEFACT_KINDS = {"code", "data", "protocol", "proof", "model", "software", "figure", "table", "prompt",
                  "trace", "manuscript", "other"}


class _Draft:
    def __init__(self):
        self.completion = []
        self.mapping = {}

    def todo(self, where, what):
        self.completion.append({"where": where, "what": what})


def _versions(st, eid):
    """IDs of every version in the correction chain containing eid, oldest first."""
    cur = st.root(eid)
    out = [cur]
    while cur in st.superseded_by:
        cur = st.superseded_by[cur]
        out.append(cur)
    return out


def _body(st, eid):
    e = st.latest(eid)
    return {} if e is None or is_withheld(e) else e["body"]


def _version_before(st, eid, seq):
    """The latest version of eid's chain written before `seq` (not withheld), or None."""
    found = None
    for v in _versions(st, eid):
        e = st.get(v)
        if e["seq"] < seq and not is_withheld(e):
            found = e
    return found


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


def final_statuses(st):
    """{hypothesis root ID: its final status event} for hypotheses that have a status."""
    out = {}
    for e in st.events:
        if e["type"] == "status" and not is_withheld(e) and e["id"] not in st.superseded_by:
            target = e["body"].get("target")
            if target:
                out[st.root(target)] = e
    return out


def export(lab):
    """Return {"draft", "completion", "mapping"}. Raises LabError if a precondition of §9 fails."""
    rep = verify(lab)
    if not rep["ok"]:
        first = rep["errors"][0]
        raise LabError("export", f"the record does not verify ({len(rep['errors'])} error(s); first: "
                                 f"[{first['rule']}] {first['message']}); run `claimset lab verify`")
    events = lab.read()
    st = State()
    for e in events:
        st.apply(e)
    unmerged = [a for a in lab.agent_logs() if a not in st.merged and lab.read_agent(a)]
    if unmerged:
        raise LabError("export", f"agent logs of {', '.join(unmerged)} are not merged; run `claimset lab merge`")
    roots = [e for e in st.events if e["id"] == st.root(e["id"])]
    hyps = [e for e in roots if e["type"] == "hypothesis"]
    statuses = final_statuses(st)
    open_ = [h["id"] for h in hyps if h["id"] not in statuses or statuses[h["id"]]["body"].get("to") not in FINAL]
    if open_:
        raise LabError("export", f"hypotheses {', '.join(open_)} have no final status (supported, not_supported, "
                                 f"inconclusive, revised or abandoned); record one with `claimset lab status`, "
                                 f"for example abandoned with a rationale")

    d = _Draft()
    anchors = read_anchors(lab)
    writes, active = {}, {}
    for e in st.events:
        if e["by"]:
            writes.setdefault(e["by"], set()).add(e["type"])
            first, last = active.get(e["by"], (e["at"], e["at"]))
            active[e["by"]] = (min(first, e["at"]), max(last, e["at"]))

    # -- contributors and models
    contributors, models, model_ids, person = [], [], {}, {}
    for a in (e for e in roots if e["type"] == "actor"):
        pid = f"P{len(contributors) + 1}"
        person[a["id"]] = pid
        d.mapping[pid] = a["id"]
        b = _body(st, a["id"])
        kind = b.get("kind", "agent")
        c = {"id": pid, "type": kind, "name": b.get("name") or "(withheld)"}
        if kind == "agent":
            authored = writes.get(a["id"], set()) - {"check", "actor"}
            c["positions"] = ["author"] if authored else ["contributor"]
            c["models"] = []
            period = active.get(a["id"])
            for m in b.get("models") or []:
                key = (m.get("provider"), m.get("name"), m.get("version"), m.get("identifier"))
                if key not in model_ids:
                    model_ids[key] = f"M{len(models) + 1}"
                    models.append({k: v for k, v in (("id", model_ids[key]), ("provider", key[0]), ("name", key[1]),
                                                     ("version", key[2]), ("identifier", key[3])) if v})
                mdef = next(x for x in models if x["id"] == model_ids[key])
                if period and (mdef.get("accessed") is None or period[0][:10] < mdef["accessed"]):
                    mdef["accessed"] = period[0][:10]
                c["models"].append({"model": model_ids[key]})
            if period:
                c["run_period"] = {"start": period[0][:10], "end": period[1][:10]}
            if "run" in writes.get(a["id"], set()):
                c["tools"] = ["code_execution"]
            if len(c["models"]) == 1:
                c["models"][0]["role"] = "sole"
            if not authored and "check" in writes.get(a["id"], set()):
                c["ai_tasks"] = [{"task": "review", "extent": "full"}]
            if b.get("record_source"):
                c["record_source"] = b["record_source"]
        else:
            c["positions"] = ["contributor"]
            if b.get("orcid"):
                c["orcid"] = b["orcid"]
            d.todo(f"contributors.{pid}", f"{c['name']} ({a['id']}) is exported as a contributor; ask whether they "
                                         f"are the supervisor and who the guarantor is (the guarantor needs an ORCID)")
        contributors.append(c)

    # -- the runs, results and deviations of the record
    runs = [e for e in st.events if e["type"] == "run"]
    deviations = {}  # (plan root, run ID) -> latest deviation event
    for e in roots:
        b = _body(st, e["id"]) if e["type"] == "deviation" else {}
        if b.get("plan") and b.get("run"):
            deviations[(st.root(b["plan"]), b["run"])] = st.latest(e["id"])
    results_of = {}
    for e in st.events:
        if e["type"] == "result" and not is_withheld(e):
            results_of.setdefault(e["body"].get("run"), []).append(e)

    claims, evidence, limitations = [], [], []
    result_ref = {}      # result event ID -> "E<n>.R<m>"
    evidence_of_run = {}  # run ID -> evidence ID

    def new_claim_id():
        return f"C{len(claims) + 1}"

    # -- claims from hypotheses
    for h in hyps:
        status = statuses[h["id"]]
        outcome = status["body"]["to"]
        if outcome not in OUTCOMES:
            continue
        cid = new_claim_id()
        cur = st.latest(h["id"])
        hb = _body(st, h["id"])
        hyp_ids = set(_versions(st, h["id"]))
        d.mapping[cid] = cur["id"]
        claim = {"id": cid, "statement": hb.get("statement") or "(withheld)", "type": "descriptive",
                 "role": "supporting", "novelty": "new", "support": [], "provenance": "author_stated",
                 "hypothesis": {"lab_event": cur["id"], "outcome": outcome}}

        cited_runs, cited_results, other = [], [], []
        for ref in status["body"].get("evidence") or []:
            ev = st.get(ref)
            if ev is None or is_withheld(ev):
                other.append(ref)
            elif ev["type"] == "run":
                cited_runs.append(ev["id"])
            elif ev["type"] == "result":
                cited_results.append(ev["id"])
                if ev["body"].get("run") not in cited_runs:
                    cited_runs.append(ev["body"]["run"])
            else:
                other.append(ref)
        if other:
            d.todo(f"claims.{cid}.support", f"the final status {status['id']} of {h['id']} also cites "
                                           f"{', '.join(other)}, which are not exported as evidence")

        run_events = sorted((st.get(r) for r in cited_runs), key=lambda e: e["seq"])
        if run_events:
            eid = f"E{len(evidence) + 1}"
            d.mapping[eid] = [r["id"] for r in run_events]
            results = []
            for r in run_events:
                evidence_of_run.setdefault(r["id"], eid)
                for res in results_of.get(r["id"], []):
                    rid = f"R{len(results) + 1}"
                    result_ref.setdefault(res["id"], f"{eid}.{rid}")
                    entry = {"id": rid}
                    entry.update({k: res["body"][k] for k in ("quantity", "value", "unit", "interval", "conditions")
                                  if res["body"].get(k) is not None})
                    results.append(entry)
            plans = []
            for r in run_events:
                p = r["body"].get("plan") if not is_withheld(r) else None
                pv = _version_before(st, p, r["seq"]) if p else None
                if pv and pv["id"] not in [x["id"] for x in plans]:
                    plans.append(pv)
            parts = [f"Lab runs {', '.join(r['id'] for r in run_events)}"
                     + (f" under plan {', '.join(p['id'] for p in plans)}." if plans else ".")]
            for p in plans:
                parts.append(f"Design: {p['body'].get('design')} Analysis: {p['body'].get('analysis')}")
            if not plans:
                parts += [f"Command: {' '.join(r['body'].get('command') or [])}" for r in run_events
                          if not is_withheld(r)]
            ev_obj = {"id": eid, "profile": "quant", "design": "other", "description": " ".join(parts),
                      "availability": {"level": "public"}, "lab_runs": [r["id"] for r in run_events]}
            if results:
                ev_obj["results"] = results
            commits = {r["body"].get("code_version") for r in run_events if not is_withheld(r)}
            execution = {}
            if len(commits) == 1 and None not in commits:
                execution["code_version"] = commits.pop()
            first = next((r for r in run_events if not is_withheld(r)), None)
            if first and (first["body"].get("environment") or {}).get("software"):
                execution["software"] = first["body"]["environment"]["software"]
            dirty = [r["id"] for r in run_events if not is_withheld(r) and r["body"].get("dirty")]
            if dirty:
                execution["notes"] = (f"Runs {', '.join(dirty)} ran with uncommitted changes in the working tree, "
                                      f"so their code version is not exactly the recorded commit.")
            if execution:
                ev_obj["execution"] = execution
            evidence.append(ev_obj)
            support = {"kind": "empirical_quant", "evidence": eid}
            cited = [result_ref[x] for x in cited_results if x in result_ref]
            if cited or results:
                support["results"] = cited or [f"{eid}.{x['id']}" for x in results]
            claim["support"].append(support)
            d.todo(f"evidence.{eid}", f"choose the profile and design of {eid} (exported as quant, design other) and "
                                      f"make its description self-contained")
        else:
            d.todo(f"claims.{cid}.support", f"no run lies behind the final status {status['id']} of {h['id']}; "
                                           f"give {cid} its support")

        # planning (§9: a run tests the hypothesis under a plan)
        tests = []
        for r in run_events:
            if is_withheld(r) or r["body"].get("label") != "confirmatory" or not r["body"].get("plan"):
                continue
            pv = _version_before(st, r["body"]["plan"], r["seq"])
            if pv and hyp_ids & set(pv["body"].get("hypotheses") or []):
                tests.append((r, pv, (st.root(pv["id"]), r["id"]) in deviations))
        clean = [t for t in tests if not t[2]]
        if clean:
            claim["planning"], chosen = "confirmatory", clean[0]
        elif tests:
            claim["planning"], chosen = "confirmatory_with_deviations", tests[0]
        else:
            claim["planning"], chosen = "exploratory", None
        if chosen:
            r, pv, _ = chosen
            claim["planned_at"] = pv["at"]
            started = r["body"].get("started_at") or r["at"]
            usable = sorted((a for a in anchors if isinstance(a, dict) and covers(a, pv)
                             and isinstance(a.get("at"), str) and a["at"] <= started), key=lambda a: a["at"])
            claim["planned_at_anchor"] = usable[0].get("service", "none") if usable else "none"
        for r, pv, deviated in tests:
            if not deviated:
                continue
            dv = deviations.get((st.root(pv["id"]), r["id"]))
            db = dv["body"] if dv else {}
            lid = f"L{len(limitations) + 1}"
            limitations.append({"id": lid, "category": "analytic_flexibility",
                                "statement": f"Lab run {r['id']} deviated from plan {pv['id']}: {db.get('text')} "
                                             f"Reason: {db.get('reason')}",
                                "affects": [cid], "impact": "could_bias_magnitude", "mitigation": "none"})
            claim.setdefault("limitations", []).append(lid)
            d.todo(f"limitations.{lid}", f"review the category, impact and wording of {lid}, exported from "
                                         f"deviation {dv['id'] if dv else '?'}")

        claim["_targets"] = hyp_ids | set(_versions(st, status["id"])) | set(cited_runs) | {
            x["id"] for r in cited_runs for x in results_of.get(r, [])}
        if outcome == "not_supported":
            d.todo(f"claims.{cid}.statement", f"{cid} is a negative result: rewrite the statement of {cur['id']} to "
                                              f"say plainly what was not found, and under what conditions")
        d.todo(f"claims.{cid}", f"confirm the type (exported as descriptive), role and novelty of {cid}, and give "
                                f"its scope")
        claims.append(claim)

    # -- checks
    for e in roots:
        if e["type"] != "check":
            continue
        cur = st.latest(e["id"])
        if is_withheld(cur):
            continue
        b = cur["body"]
        target = b.get("target")
        matched = [c for c in claims if target in c.get("_targets", set())]
        if not matched:
            d.todo("claims", f"check {cur['id']} targets {target}, which no exported claim rests on; it is not exported")
        for c in matched:
            entry = {"by": person.get(cur["by"]), "outcome": b.get("outcome"), "extent": b.get("extent")}
            entry.update({k: b[k] for k in ("notes", "resolution") if b.get(k)})
            c.setdefault("checks", []).append(entry)
            if any(x["id"] == entry["by"] and "author" in x["positions"] for x in contributors):
                d.todo(f"claims.{c['id']}.checks", f"check {cur['id']} was made by an author agent "
                                                   f"({cur['by']}); a claimset check must come from a non-author")

    # -- assumptions
    for a in roots:
        if a["type"] != "assumption":
            continue
        cid = new_claim_id()
        cur = st.latest(a["id"])
        b = _body(st, a["id"])
        d.mapping[cid] = cur["id"]
        claim = {"id": cid, "statement": b.get("statement") or "(withheld)", "type": "descriptive",
                 "role": "supporting", "novelty": "new", "support": [{"kind": "assumption"}],
                 "assumption_category": b.get("category"), "check": b.get("check"), "provenance": "author_stated"}
        ce = b.get("check_evidence")
        if ce:
            ce_event = st.get(ce)
            run_id = ce if ce_event and ce_event["type"] == "run" else (
                _body(st, ce).get("run") if ce_event and ce_event["type"] == "result" else None)
            if run_id in evidence_of_run:
                claim["check_evidence"] = evidence_of_run[run_id]
            else:
                d.todo(f"claims.{cid}.check_evidence", f"assumption {cur['id']} was checked by {ce}, which is not "
                                                       f"exported as evidence; give {cid} its check_evidence")
        claims.append(claim)
    if any("assumption_category" in c for c in claims):
        d.todo("claims[].assumptions", "link each claim to the assumption claims it relies on")

    for c in claims:
        c.pop("_targets", None)

    # -- artefacts and the lab record itself
    artefacts = []
    for a in roots:
        if a["type"] != "artefact":
            continue
        b = _body(st, a["id"])
        aid = f"A{len(artefacts) + 1}"
        d.mapping[aid] = st.latest(a["id"])["id"]
        art = {"id": aid, "kind": b.get("kind") if b.get("kind") in ARTEFACT_KINDS else "other",
               "uri": b.get("path_or_uri"), "availability": {"level": "public"}}
        art.update({k: v for k, v in (("version", b.get("hash")), ("licence", b.get("licence")),
                                      ("description", b.get("description"))) if v})
        artefacts.append(art)
    lab_aid = f"A{len(artefacts) + 1}"
    init = _body(st, "INIT")
    rel = _repo_path(lab.log_path) or f"{lab.root.name}/{lab.log_path.name}"
    repo = init.get("repository")
    lab_art = {"id": lab_aid, "kind": "lab_record", "uri": f"{repo.rstrip('/')}/{rel}" if repo else rel,
               "version": file_hash(lab.log_path), "availability": {"level": "public"},
               "description": "Lab record of the study: questions, hypotheses, plans, runs, results and decisions, "
                              "as an append-only, hash-chained event log (Claimset lab record specification v0.4).",
               "withheld_events": [{"event": e["id"], "reason": e["body"].get("reason"),
                                    "comment": e["body"].get("comment")} for e in st.events if is_withheld(e)]}
    if not lab_art["withheld_events"]:
        del lab_art["withheld_events"]
    artefacts.append(lab_art)
    d.mapping[lab_aid] = "lab.jsonl"
    if not repo:
        d.todo(f"artefacts.{lab_aid}.uri", "give the public URI of the lab record (the record has no repository URI)")
    for art in artefacts:
        if "licence" not in art:
            d.todo(f"artefacts.{art['id']}.licence", f"give the licence of {art['id']}")

    # -- exploration and paper
    counts = {k: 0 for k in ("supported", "not_supported", "inconclusive", "revised", "abandoned")}
    for h in hyps:
        counts[statuses[h["id"]]["body"]["to"]] += 1
    visible = [r for r in runs if not is_withheld(r)]
    exploration = {
        "lab_record": lab_aid,
        "questions": sum(1 for e in roots if e["type"] == "question"),
        "hypotheses": dict({"total": sum(counts.values())}, **counts),
        "runs": {"total": len(runs),
                 "confirmatory": sum(r["body"].get("label") == "confirmatory" for r in visible),
                 "exploratory": len(runs) - sum(r["body"].get("label") == "confirmatory" for r in visible),
                 "failed": sum(r["body"].get("exit_status") != "success" for r in visible)},
    }
    paper = {"title": init.get("working_title") or "(untitled)", "contributions": [], "autonomy": "unknown",
             "contribution_record": "complete", "origin": "native", "exploration": exploration}
    d.completion[:0] = [
        {"where": "paper.licence", "what": "ask the supervisor or guarantor which licence applies (SPDX identifier)"},
        {"where": "paper.contributions", "what": "choose the principal claims, set their role to principal and "
                                                 "list them in priority order"},
        {"where": "paper.autonomy", "what": "set autonomy (exported as unknown): autonomous, human_supervised or "
                                            "human_assisted"},
        {"where": "paper.classification.keywords", "what": "propose keywords"},
        {"where": "paper.contribution_record", "what": "confirm that every contributor is listed (exported as "
                                                       "complete: everyone who wrote to the lab record)"},
    ]
    draft = {"schema_version": SPEC_VERSION, "paper": paper, "contributors": contributors, "models": models,
             "definitions": [], "claims": claims, "evidence": evidence, "warrants": [],
             "limitations": limitations, "artefacts": artefacts}
    return {"draft": draft, "completion": d.completion, "mapping": d.mapping}
