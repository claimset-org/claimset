"""Current state of a lab record, for whoever picks up the work (spec §2, principle 8)."""

from .core import State, final_statuses, is_withheld

OPEN_STATUSES = ("proposed", "testing")


def _text(event):
    if event is None or is_withheld(event):
        return "(withheld)"
    b = event["body"]
    for k in ("statement", "text", "working_title", "name", "path_or_uri"):
        if b.get(k):
            return b[k]
    if event["type"] == "status":
        return f"{b.get('target')} -> {b.get('to')}: {b.get('rationale', '')}"
    if event["type"] == "check":
        return f"{b.get('target')}: {b.get('outcome')} ({b.get('extent')})"
    if event["type"] == "run":
        return f"[{b.get('label')}, {b.get('exit_status')}] {' '.join(b.get('command') or [])}"
    if event["type"] == "result":
        return f"{b.get('quantity')} = {b.get('value')} ({b.get('run')})"
    if event["type"] == "plan":
        return f"[{b.get('kind')}] {b.get('design', '')}"
    if event["type"] == "approval":
        return f"approves {b.get('target')}"
    if event["type"] == "deviation":
        return f"{b.get('run')} vs {b.get('plan')}: {b.get('text', '')}"
    if event["type"] == "merge":
        return f"{b.get('events_merged')} events from {', '.join(b.get('agents') or [])}"
    return ""


def build(events):
    """Summarise a list of events into a plain dict."""
    st = State()
    for e in events:
        st.apply(e)

    roots = [e for e in st.events if e["id"] == st.root(e["id"])]

    def by_type(t):
        return [e for e in roots if e["type"] == t]

    hyp_status = final_statuses(st)

    hypotheses = []
    for h in by_type("hypothesis"):
        cur = st.latest(h["id"])
        s = hyp_status.get(h["id"])
        body = {} if is_withheld(cur) else cur["body"]
        hypotheses.append({
            "id": h["id"],
            "statement": _text(cur),
            "question": body.get("question"),
            "origin": body.get("origin"),
            "status": s["body"]["to"] if s else "proposed",
            "status_event": s["id"] if s else None,
            "rationale": s["body"].get("rationale") if s else None,
            "evidence": s["body"].get("evidence", []) if s else [],
            "revised_to": s["body"].get("revised_to") if s else None,
            "corrected_by": cur["id"] if cur["id"] != h["id"] else None,
        })

    checks = []
    for c in by_type("check"):
        cur = st.latest(c["id"])
        if not is_withheld(cur):
            b = cur["body"]
            checks.append({"id": c["id"], "target": b.get("target"), "outcome": b.get("outcome"),
                           "extent": b.get("extent"), "by": cur["by"]})

    counts = {}
    for e in st.events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1

    init = st.get("INIT")

    def items(t, last=None):
        rows = [{"id": e["id"], "text": _text(st.latest(e["id"])), "by": e["by"], "at": e["at"]}
                for e in by_type(t)]
        return rows[-last:] if last else rows

    runs = [e for e in st.events if e["type"] == "run" and not is_withheld(e)]
    approvals = {e["body"].get("target") for e in st.events
                 if e["type"] == "approval" and not is_withheld(e)}
    plans = []
    for p in by_type("plan"):
        cur = st.latest(p["id"])
        if is_withheld(cur):
            continue
        under = [r for r in runs if st.root(r["body"].get("plan") or "") == p["id"]]
        plans.append({"id": p["id"], "kind": cur["body"].get("kind"),
                      "hypotheses": cur["body"].get("hypotheses", []), "design": cur["body"].get("design"),
                      "approved": bool({p["id"], cur["id"]} & approvals),
                      "runs": [r["id"] for r in under],
                      "done": any(r["body"].get("exit_status") == "success" for r in under)})
    results = [e for e in st.events if e["type"] == "result" and not is_withheld(e)]

    return {
        "title": _text(init) if init else None,
        "study": st.study,
        "papers": [{"id": st.root(e["id"]), "name": e["body"].get("name"), "questions": e["body"].get("questions")}
                   for e in st.papers.values()],
        "events": len(st.events),
        "counts": counts,
        "actors": [{"id": a["id"], "kind": st.latest(a["id"])["body"].get("kind"), "name": _text(st.latest(a["id"]))}
                   for a in by_type("actor") if not is_withheld(st.latest(a["id"]))],
        "questions": items("question"),
        "hypotheses": hypotheses,
        "open_hypotheses": [h["id"] for h in hypotheses if h["status"] in OPEN_STATUSES],
        "assumptions": [{"id": a["id"], "statement": _text(st.latest(a["id"])),
                         "check": None if is_withheld(st.latest(a["id"])) else st.latest(a["id"])["body"].get("check")}
                        for a in by_type("assumption")],
        "recent_decisions": items("decision", last=5),
        "recent_notes": items("note", last=5),
        "checks": checks,
        "artefacts": items("artefact"),
        "plans": plans,
        "pending_plans": [p["id"] for p in plans if not p["done"]],
        "runs": {"total": len(runs),
                 "confirmatory": sum(r["body"].get("label") == "confirmatory" for r in runs),
                 "exploratory": sum(r["body"].get("label") == "exploratory" for r in runs),
                 "failed": sum(r["body"].get("exit_status") != "success" for r in runs)},
        "recent_results": [{"id": r["id"], "run": r["body"].get("run"), "quantity": r["body"].get("quantity"),
                            "value": r["body"].get("value"), "conditions": r["body"].get("conditions")}
                           for r in results[-5:]],
    }


def render(s):
    """Plain-text rendering of a summary."""
    out = [f"Lab: {s['title']}  ({s['events']} events)" + (f"; study {s['study']}" if s.get("study") else "")]
    if s.get("papers"):
        out.append("Papers: " + "; ".join(f"{p['name']} ({', '.join(p['questions'] or [])})" for p in s["papers"]))
    v = s.get("view")
    if v:
        out.append(f"Agent log of {v['actor']}: {v['own_events']} own events on top of main seq {v['base']}"
                   + (f"; the main log has {v['newer_main_events']} newer events, not visible here until "
                      f"this log is merged" if v["newer_main_events"] else ""))
    logs = s.get("agent_logs") or {}
    if logs:
        out.append("Agent logs: " + "; ".join(
            f"{a} " + (f"merged by {x['merged_by']}" if x["merged_by"] else f"open, {x['events']} events")
            for a, x in logs.items()))
    if s["actors"]:
        out.append("Actors: " + "; ".join(f"{a['id']} {a['name']} ({a['kind']})" for a in s["actors"]))
    qs = {q["id"]: q for q in s["questions"]}
    out.append("")
    if not s["questions"]:
        out.append("No questions yet.")
    for q in s["questions"]:
        out.append(f"{q['id']}: {q['text']}")
        for h in [h for h in s["hypotheses"] if h["question"] == q["id"]]:
            extra = f" -> {h['revised_to']}" if h["revised_to"] else ""
            corr = f" (corrected by {h['corrected_by']})" if h["corrected_by"] else ""
            out.append(f"  {h['id']} [{h['status']}{extra}] {h['statement']}{corr}")
    orphans = [h for h in s["hypotheses"] if h["question"] not in qs]
    for h in orphans:
        out.append(f"  {h['id']} [{h['status']}] {h['statement']}")
    out.append("")
    out.append("Open hypotheses: " + (", ".join(s["open_hypotheses"]) or "none"))
    if s["assumptions"]:
        out.append("Assumptions: " + "; ".join(f"{a['id']} ({a['check']})" for a in s["assumptions"]))
    if s["plans"]:
        out.append("Plans:")
        for p in s["plans"]:
            flags = ("approved, " if p["approved"] else "") + (f"runs {' '.join(p['runs'])}" if p["runs"] else "no runs yet")
            out.append(f"  {p['id']} [{p['kind']}] tests {', '.join(p['hypotheses']) or '-'} ({flags})")
    r = s["runs"]
    if r["total"]:
        out.append(f"Runs: {r['total']} ({r['confirmatory']} confirmatory, {r['exploratory']} exploratory, "
                   f"{r['failed']} not successful)")
    if s["recent_results"]:
        out.append("Recent results:")
        out += [f"  {x['id']} ({x['run']}) {x['quantity']} = {x['value']}" for x in s["recent_results"]]
    if s["checks"]:
        out.append("Checks: " + "; ".join(f"{c['id']} {c['target']} {c['outcome']}" for c in s["checks"]))
    if s["recent_decisions"]:
        out.append("Recent decisions:")
        out += [f"  {d['id']} {d['text']}" for d in s["recent_decisions"]]
    if s["recent_notes"]:
        out.append("Recent notes:")
        out += [f"  {n['id']} {n['text']}" for n in s["recent_notes"]]
    return "\n".join(out)


def list_line(event):
    """One line per event for `claimset lab list`."""
    sup = f" (corrects {event['supersedes']})" if event.get("supersedes") else ""
    text = _text(event)
    if len(text) > 100:
        text = text[:97] + "..."
    return f"{event['seq']:>5} {event['id']:<10} {event['type']:<11} {event['by'] or '-':<6} {event['at']}  {text}{sup}"
