"""Renumbering a claimset in reading order before its first submission (spec §9, *Order and IDs*).

Claims are ordered by role (principal, supporting, secondary, then assumptions), each group by the first place
the paper makes the claim (its earliest source span); claims without spans keep their file order within their
group. Warrants follow the order of the claims that use them; definitions, limitations and open questions keep
their order and lose their gaps. Every reference in the file is rewritten: ID fields, lists of IDs, the
dimensions and values of observations (table columns, row labels, cells and the keys and values of conditions),
the targets of relations within the paper (`id_type: local`) and the [[Dn:term]] marks in text. Lab-record IDs
(`lab_event`, `lab_runs`), source spans, the targets of relations to other works and text are left alone.
"""

import re

SPAN = re.compile(r"p(\d+)\.(\d+)")
MARK = re.compile(r"\[\[(D\d+):")
ROLE_ORDER = {"principal": 0, "supporting": 1, "secondary": 2}


def first_span(obj):
    """(page, paragraph) of the earliest source span, or a key that sorts last."""
    found = [(int(m.group(1)), int(m.group(2))) for sp in obj.get("source_spans") or [] for m in SPAN.finditer(sp)]
    return min(found) if found else (10 ** 6, 0)


def is_assumption(claim):
    return any(isinstance(s, dict) and s.get("kind") == "assumption" for s in claim.get("support") or [])


def claim_key(claim):
    group = 3 if is_assumption(claim) else ROLE_ORDER.get(claim.get("role"), 4)
    return (group, first_span(claim))


def plan(doc):
    """The old-to-new ID map."""
    mapping = {}
    claims = sorted(doc.get("claims") or [], key=claim_key)     # stable: ties keep file order
    for n, c in enumerate(claims, 1):
        mapping[c["id"]] = f"C{n}"
    warrants = {w["id"]: w for w in doc.get("warrants") or []}
    used = []
    for c in claims:
        for s in c.get("support") or []:
            w = s.get("warrant") if isinstance(s, dict) else None
            if w in warrants and w not in used:
                used.append(w)
        if c.get("warrant") in warrants and c["warrant"] not in used:
            used.append(c["warrant"])
    used += [w for w in warrants if w not in used]
    for n, w in enumerate(used, 1):
        mapping[w] = f"W{n}"
    for key, prefix in (("definitions", "D"), ("limitations", "L"), ("open_questions", "O")):
        for n, x in enumerate(doc.get(key) or [], 1):
            mapping[x["id"]] = f"{prefix}{n}"
    return mapping


KEEP = {"lab_event", "lab_runs", "source_spans", "target"}


def rewrite(x, mapping):
    if isinstance(x, dict):
        out = {mapping.get(k, k): (v if k in KEEP else rewrite(v, mapping)) for k, v in x.items()}
        if x.get("id_type") == "local" and isinstance(x.get("target"), str):
            out["target"] = mapping.get(x["target"], x["target"])          # a claim of this paper
        return out
    if isinstance(x, list):
        return [rewrite(v, mapping) for v in x]
    if isinstance(x, str):
        if x in mapping:
            return mapping[x]
        return MARK.sub(lambda m: f"[[{mapping.get(m.group(1), m.group(1))}:", x)
    return x


def published(doc):
    """A published claimset (or a correction or revision of one): its IDs are fixed."""
    paper = doc.get("paper") if isinstance(doc.get("paper"), dict) else {}
    return bool(paper.get("id") or paper.get("version"))


def renumber(doc):
    """(renumbered document, old-to-new map). Claims and warrants are written in their new order."""
    mapping = plan(doc)
    out = rewrite(doc, mapping)
    order = {new: i for i, new in enumerate(mapping.values())}
    for key in ("claims", "warrants"):
        if key in out:
            out[key] = sorted(out[key], key=lambda o: order[o["id"]])
    paper = out.get("paper") or {}
    if isinstance(paper.get("contributions"), list):
        paper["contributions"] = sorted(paper["contributions"], key=lambda x: order.get(x, len(order)))
    return out, mapping
