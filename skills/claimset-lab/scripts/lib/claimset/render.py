"""The reading view: a plain-text rendering of a claimset for reading models (spec §2.3).

The view is derived mechanically from a valid claimset file. It keeps what a reader needs to
understand, use or challenge the paper's claims, and leaves out what serves tools, audit and
publication (provenance, source spans, availability, artefact locations and licences,
contributors, models, lab identifiers). It adds nothing: every line comes from a field of the file.
"""

import json


def _s(x):
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)


def _num(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v)) if abs(v) < 1e15 else repr(v)
    return str(v)


def _join(items, sep="; "):
    return sep.join(i for i in items if i)


def _interval(iv):
    if not isinstance(iv, dict):
        return ""
    level = iv.get("level")
    lvl = f"{_num(level * 100 if isinstance(level, (int, float)) and level <= 1 else level)}% " if level is not None else ""
    return f"{lvl}interval {_num(iv.get('lower'))} to {_num(iv.get('upper'))}"


def _conditions(c):
    if not isinstance(c, dict) or not c:
        return ""
    return "{" + ", ".join(f"{k}: {_s(v)}" for k, v in c.items()) + "}"


def _support(s):
    k = s.get("kind", "")
    ptr = [s.get("evidence"), s.get("warrant"), s.get("reference"), s.get("holder")]
    if s.get("target"):
        ptr.append(f"{s.get('id_type', '')} {s['target']}".strip())
    out = " ".join([k] + [p for p in ptr if p])
    if s.get("results"):
        out += " (" + ", ".join(s["results"]) + ")"
    if s.get("artefacts"):
        out += " artefacts " + ", ".join(s["artefacts"])
    if s.get("locator"):
        out += f", at {s['locator']}"
    if s.get("self"):
        out += ", own earlier work"
    return out


def _scope(sc):
    if not isinstance(sc, dict):
        return ""
    parts = []
    for k, v in sc.items():
        if isinstance(v, list):
            if v:
                parts.append(f"{k}: " + "; ".join(_s(x) for x in v))
        elif v not in (None, ""):
            parts.append(f"{k}: {_s(v)}")
    return " | ".join(parts)


def _claim(c):
    head = [c.get("type"), c.get("role")]
    if c.get("novelty") and c.get("novelty") != "new":
        head.append(c["novelty"])
    if c.get("assumption_category"):
        head.append(f"assumption ({c['assumption_category']}, {c.get('check', 'unchecked')})")
    sup = [_support(s) for s in c.get("support") or [] if isinstance(s, dict) and s.get("kind") != "assumption"]
    if sup:
        head.append("support: " + ", ".join(sup))
    for key, label in (("assumptions", "assumes"), ("depends_on", "uses"), ("limitations", "limitations")):
        if c.get(key):
            head.append(f"{label} " + ", ".join(c[key]))
    if c.get("warrant"):
        head.append(f"warrant {c['warrant']}")
    strengths = [f"{a.get('strength')} ({a.get('by')})" for a in c.get("assessments") or [] if isinstance(a, dict)]
    if strengths:
        head.append("strength " + ", ".join(strengths))
    for key in ("verification", "verifiability", "planning"):
        if c.get(key):
            head.append(f"{key} {c[key]}")
    if isinstance(c.get("hypothesis"), dict) and c["hypothesis"].get("outcome"):
        head.append(f"outcome {c['hypothesis']['outcome']}")
    lines = [f"{c.get('id')} [{_join(head)}]", f"  {c.get('statement', '')}"]
    if c.get("scope"):
        lines.append(f"  scope: {_scope(c['scope'])}")
    for rel in c.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        target = rel.get("reference") or f"{rel.get('id_type', '')} {rel.get('target', '')}".strip()
        loc = f", at {rel['locator']}" if rel.get("locator") else ""
        note = f": {rel['note']}" if rel.get("note") else ""
        lines.append(f"  {rel.get('type')} {target}{loc}{note}")
    t = c.get("test")
    if isinstance(t, dict):
        params = "; ".join(f"{k} {_s(v)}" for k, v in (t.get("parameters") or {}).items())
        lines.append("  test: " + _join([t.get("framework"), params,
                                          f"supported if {t['supported']}" if t.get("supported") else "",
                                          f"not supported if {t['not_supported']}" if t.get("not_supported") else "",
                                          f"verdict {t['verdict']}" if t.get("verdict") else "",
                                          ("flags " + ", ".join(t["flags"])) if t.get("flags") else ""]))
    return lines


def _table(eid, t):
    lines = [f"  {eid}.{t.get('id')} {t.get('description', '')}"
             + (f" {_conditions(t.get('conditions'))}" if t.get("conditions") else "")]
    cols = list(t.get("columns") or [])
    units = t.get("units")
    if isinstance(units, list):
        cols = [f"{c} ({u})" if u else c for c, u in zip(cols, units)]
    lines.append("  | " + " | ".join(["row"] + list(t.get("row_labels") or []) + cols) + " |")
    for r in t.get("rows") or []:
        if isinstance(r, dict):
            cells = [r.get("id", "")] + [_s(x) for x in r.get("label") or []] + [_num(v) for v in r.get("values") or []]
            lines.append("  | " + " | ".join(cells) + " |")
    return lines


def _evidence(e):
    head = [e.get("profile")]
    for key in ("design", "data_origin", "analytic_approach", "sampling", "foundation", "checker"):
        if e.get(key):
            head.append(f"{key} {_s(e[key])}")
    for key in ("n", "n_participants_or_cases", "runs"):
        if e.get(key) is not None:
            head.append(f"{key} {_num(e[key])}")
    if e.get("preregistered"):
        head.append("preregistered")
    lines = [f"{e.get('id')} [{_join(head)}] {e.get('description', '')}"]
    if e.get("estimate") is not None:
        lines.append(f"  estimate: {e.get('estimand', '')} = {_num(e['estimate'])}"
                     + (f" ({_interval(e.get('interval'))})" if e.get("interval") else ""))
    if e.get("assumptions"):
        lines.append("  assumes " + ", ".join(e["assumptions"]))
    for r in e.get("results") or []:
        if isinstance(r, dict):
            extra = _join([r.get("unit"), _interval(r.get("interval")), _conditions(r.get("conditions"))], " ")
            lines.append(f"  {e.get('id')}.{r.get('id')} {r.get('quantity')} = {_num(r.get('value'))}"
                         + (f" {extra}" if extra else ""))
    for t in e.get("tables") or []:
        if isinstance(t, dict):
            lines += _table(e.get("id"), t)
    for s in e.get("steps") or []:
        if isinstance(s, dict):
            lines.append(f"  {s.get('id')} {s.get('statement')} (by {', '.join(s.get('by') or []) or 'none'}; {s.get('rule')})")
    if e.get("profile") == "formal" and not e.get("steps") and e.get("source_spans"):
        lines.append("  derivation: in the source")
    for key in ("exemplars", "reflexivity", "outputs", "parameters", "seeds"):
        if e.get(key):
            lines.append(f"  {key}: {_s(e[key])}")
    return lines


def render(doc):
    """The reading view of a claimset, as a string."""
    p = doc.get("paper") or {}
    out = [f"CLAIMSET {p.get('title', '')}"]
    meta = [f"licence {p['licence']}" if p.get("licence") else ""]
    cf = p.get("converted_from")
    if isinstance(cf, dict):
        authors = ", ".join(a.get("name", "") for a in cf.get("source_authors") or [] if isinstance(a, dict))
        meta.append(f"converted from {cf.get('source_id')} {cf.get('source_version', '')}".strip()
                    + (f" by {authors}" if authors else ""))
    if p.get("contributions"):
        meta.append("contributions " + ", ".join(p["contributions"]))
    out.append(_join(meta))
    if isinstance(p.get("exploration"), dict):
        out.append("exploration " + json.dumps(p["exploration"], ensure_ascii=False, separators=(", ", ": ")))

    sections = [
        ("DEFINITIONS", doc.get("definitions"),
         lambda d: [f"{d.get('id')} {d.get('term')}: {d.get('definition')}"
                    + (f" [source {_s(d['source']) if not isinstance(d['source'], dict) else d['source'].get('target')}]"
                       if d.get("source") else "")]),
        ("CLAIMS", doc.get("claims"), _claim),
        ("EVIDENCE", doc.get("evidence"), _evidence),
        ("WARRANTS", doc.get("warrants"),
         lambda w: [f"{w.get('id')} [{_join([w.get('reasoning'), ('premises ' + ', '.join(w['premises'])) if w.get('premises') else ''])}] {w.get('text', '')}"]),
        ("LIMITATIONS", doc.get("limitations"),
         lambda l: [f"{l.get('id')} [{_join([l.get('category'), ('affects ' + ', '.join(l.get('affects') or [])), l.get('impact'), ('mitigation ' + l['mitigation']) if l.get('mitigation') else ''])}] {l.get('statement', '')}"]),
        ("ARTEFACTS", [a for a in doc.get("artefacts") or [] if isinstance(a, dict) and a.get("description")],
         lambda a: [f"{a.get('id')} [{a.get('kind')}] {a.get('description')}"]),
        ("REFERENCES", doc.get("references"),
         lambda b: [f"{b.get('id')} {b.get('target')}" + (f" ({b['id_type']})" if b.get("id_type") not in (None, "reference") else "")]),
    ]
    for title, items, fn in sections:
        items = [x for x in items or [] if isinstance(x, dict)]
        if items:
            out += ["", title]
            for x in items:
                out += fn(x)
    return "\n".join(out) + "\n"
