"""Current state of a lab record, for whoever picks up the work (spec §2, principle 8)."""

from .core import State, is_withheld

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
    return ""


def build(events):
    """Summarise a list of events into a plain dict."""
    st = State()
    for e in events:
        st.apply(e)

    roots = [e for e in st.events if e["id"] == st.root(e["id"])]

    def by_type(t):
        return [e for e in roots if e["type"] == t]

    hyp_status = {}
    for e in st.events:
        if e["type"] == "status" and not is_withheld(e) and e["id"] not in st.superseded_by:
            hyp_status[st.root(e["body"].get("target"))] = e

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

    return {
        "title": _text(init) if init else None,
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
    }


def render(s):
    """Plain-text rendering of a summary."""
    out = [f"Lab: {s['title']}  ({s['events']} events)"]
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
    return f"{event['seq']:>5} {event['id']:<8} {event['type']:<11} {event['by'] or '-':<6} {event['at']}  {text}{sup}"
