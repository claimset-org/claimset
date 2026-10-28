"""The reading view: a plain-text rendering of a claimset for reading models (spec §2.3).

The view is derived mechanically from a valid claimset file. It keeps what a reader needs to
understand, use or challenge the paper's claims, and leaves out what serves tools, audit and
publication (provenance, source spans, availability, artefact locations and licences,
contributors, models, lab identifiers). It adds nothing: every line comes from a field of the file.
"""

import json

from .validate import inherited_assumptions


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


def _val(v):
    """An observation's value: a number, a category, yes/no, or an excerpt with its locator, gloss and translation."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, dict) and "text" in v:
        out = f"\"{v.get('text')}\""
        if v.get("language"):
            out += f" [{v['language']}]"
        if v.get("gloss"):
            out += f" (gloss: {v['gloss']})"
        if v.get("translation"):
            out += f" ('{v['translation']}')"
        if v.get("locator"):
            out += f" ({v['locator']})"
        return out
    return "" if v is None else _s(v)


def _column(c):
    if isinstance(c, dict):
        return _join([_s(c.get("variable")), _conditions(c.get("conditions"))], " ")
    return _s(c)


def _declarations(d):
    if not isinstance(d, dict):
        return ""
    parts = []
    if "funding" in d:
        fund = [_join([f.get("funder"), f"({f['funder_id']})" if f.get("funder_id") else "",
                       f"award {f['award']}" if f.get("award") else ""], " ")
                for f in d.get("funding") or [] if isinstance(f, dict)]
        parts.append("funding: " + ("; ".join(fund) if fund else "none"))
    if d.get("competing_interests"):
        parts.append(f"competing interests: {d['competing_interests']}")
    if "ethics" in d:
        eth = [_join([x.get("body"), x.get("reference"), x.get("decision")], ", ")
               for x in d.get("ethics") or [] if isinstance(x, dict)]
        parts.append("ethics: " + ("; ".join(eth) if eth else "not reviewed"))
    if d.get("consent"):
        parts.append(f"consent: {d['consent']}")
    return " | ".join(parts)


def _definition(d):
    tags = [("members", "members"), ("part_of", "part of"), ("broader", "a kind of"), ("assumptions", "assumes"),
            ("identifiers", "identifiers")]
    line = f"{d.get('id')} " + (f"[{d['kind']}] " if d.get("kind") else "") + f"{d.get('term')}: {d.get('definition')}"
    for key, label in tags:
        if d.get(key):
            line += f" [{label} {', '.join(_s(x) for x in d[key])}]"
    if d.get("source"):
        line += f" [source {_s(d['source']) if not isinstance(d['source'], dict) else d['source'].get('target')}]"
    return [line]


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


def _claim(c, inherited=()):
    head = [c.get("type"), c.get("role")]
    if c.get("novelty") and c.get("novelty") != "no_prior_found":
        head.append(c["novelty"])
    if c.get("assumption_category"):
        head.append(f"assumption ({c['assumption_category']}, {c.get('check', 'unchecked')})")
    sup = [_support(s) for s in c.get("support") or [] if isinstance(s, dict) and s.get("kind") != "assumption"]
    if sup:
        head.append("support: " + ", ".join(sup))
    for key, label in (("assumptions", "assumes"), ("depends_on", "uses"), ("limitations", "limitations")):
        if c.get(key):
            head.append(f"{label} " + ", ".join(c[key]))
    inherited = [a for a in inherited if a not in (c.get("assumptions") or [])]
    if inherited:
        head.append("inherits " + ", ".join(inherited))
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
        target = rel.get("reference") or (rel.get("target", "") if rel.get("id_type") == "local"
                                          else f"{rel.get('id_type', '')} {rel.get('target', '')}".strip())
        loc = f", at {rel['locator']}" if rel.get("locator") else ""
        note = f": {rel['note']}" if rel.get("note") else ""
        own = ", own earlier work" if rel.get("self") else ""
        lines.append(f"  {rel.get('type')} {target}{loc}{own}{note}")
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
    cols = [_column(c) for c in t.get("columns") or []]
    units = t.get("units")
    if isinstance(units, list):
        cols = [f"{c} ({u})" if u else c for c, u in zip(cols, units)]
    lines.append("  | " + " | ".join(["row"] + list(t.get("row_labels") or []) + cols) + " |")
    for r in t.get("rows") or []:
        if isinstance(r, dict):
            cells = [r.get("id", "")] + [_s(x) for x in r.get("label") or []] + [_val(v) for v in r.get("values") or []]
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
    setup = e.get("setup") if isinstance(e.get("setup"), dict) else {}
    parts = [f"{role} {', '.join(str(d) for d in setup[role])}" for role in ("studied", "cases", "procedure", "lens", "context")
             if isinstance(setup.get(role), list) and setup[role]]
    if parts:
        lines.append(f"  setup: {'; '.join(parts)}")
    derived = [f"{x.get('evidence')} ({x.get('how')})" for x in e.get("derived_from") or [] if isinstance(x, dict)]
    if derived:
        lines.append("  derived from " + ", ".join(derived))
    if e.get("estimate") is not None:
        lines.append(f"  estimate: {e.get('estimand', '')} = {_num(e['estimate'])}"
                     + (f" ({_interval(e.get('interval'))})" if e.get("interval") else ""))
    if e.get("assumptions"):
        lines.append("  assumes " + ", ".join(e["assumptions"]))
    for r in e.get("results") or []:
        if isinstance(r, dict):
            rf = r.get("read_from") if isinstance(r.get("read_from"), dict) else {}
            extra = _join([r.get("unit"), _interval(r.get("interval")),
                           f"n {_num(r['n'])}" if r.get("n") is not None else "", _conditions(r.get("conditions")),
                           f"read from {rf.get('artefact')}, precision {_s(rf.get('precision'))}" if rf else ""], " ")
            lines.append(f"  {e.get('id')}.{r.get('id')} {_s(r.get('variable'))} = {_val(r.get('value'))}"
                         + (f" {extra}" if extra else ""))
    for t in e.get("tables") or []:
        if isinstance(t, dict):
            lines += _table(e.get("id"), t)
    for s in e.get("steps") or []:
        if isinstance(s, dict):
            lines.append(f"  {s.get('id')} {s.get('statement')} (by {', '.join(s.get('by') or []) or 'none'}; {s.get('rule')})")
    if e.get("profile") == "formal" and not e.get("steps") and e.get("source_spans"):
        lines.append("  derivation: in the source")
    for key in ("reflexivity", "outputs", "parameters", "seeds"):
        if e.get(key):
            lines.append(f"  {key}: {_s(e[key])}")
    return lines


def _without_evidence(c):
    """True if the claim's only support is argument or none (spec §2.3)."""
    kinds = {s.get("kind") for s in c.get("support") or [] if isinstance(s, dict)}
    return bool(kinds) and kinds <= {"argument", "unstated"}


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
    if p.get("declarations"):
        out.append("declarations " + _declarations(p["declarations"]))
    if isinstance(p.get("exploration"), dict):
        out.append("exploration " + json.dumps(p["exploration"], ensure_ascii=False, separators=(", ", ": ")))

    claims = [c for c in doc.get("claims") or [] if isinstance(c, dict)]
    defs = {d.get("id"): d for d in doc.get("definitions") or [] if isinstance(d, dict)}
    evid = {e.get("id"): e for e in doc.get("evidence") or [] if isinstance(e, dict)}

    def inherited(c):
        """Assumptions a claim inherits from the terms it rests on (§3.2)."""
        return inherited_assumptions(c, defs, evid)
    unevidenced = [c for c in claims if c.get("role") != "principal" and _without_evidence(c)]
    order = {"principal": 0, "supporting": 1, "secondary": 2}
    main = sorted((c for c in claims if not any(c is u for u in unevidenced)),
                  key=lambda c: order.get(c.get("role"), 3))           # stable: file order within a role
    sections = [
        ("DEFINITIONS", doc.get("definitions"), _definition),
        ("CLAIMS", main, lambda c: _claim(c, inherited(c))),
        ("CLAIMS WITHOUT EVIDENCE", unevidenced, lambda c: _claim(c, inherited(c))),
        ("EVIDENCE", doc.get("evidence"), _evidence),
        ("WARRANTS", doc.get("warrants"),
         lambda w: [f"{w.get('id')} [{_join([w.get('reasoning'), ('premises ' + ', '.join(w['premises'])) if w.get('premises') else ''])}] {w.get('text', '')}"]),
        ("LIMITATIONS", doc.get("limitations"),
         lambda l: [f"{l.get('id')} [{_join([l.get('category'), ('affects ' + ', '.join(l.get('affects') or [])), l.get('impact'), ('mitigation ' + l['mitigation']) if l.get('mitigation') else ''])}] {l.get('statement', '')}"]),
        ("OPEN QUESTIONS", doc.get("open_questions"),
         lambda q: [f"{q.get('id')}" + (f" [from {', '.join(q['claims'])}]" if q.get("claims") else "")
                    + f" {q.get('question', '')}"]),
        ("ARTEFACTS", [a for a in doc.get("artefacts") or [] if isinstance(a, dict) and a.get("description")],
         lambda a: [f"{a.get('id')} [{_join([a.get('kind'), a.get('function')], ', ')}] {a.get('description')}"
                    + (f" shows {', '.join(_s(x) for x in a['shows'])}" if a.get("shows") else "")]),
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
