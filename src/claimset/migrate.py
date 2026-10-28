"""Bringing a claimset written under v0.23 to v0.30 to the current version (spec, Changes since v0.29).

Only what is mechanical is changed: the roles of each evidence `setup` are renamed (compared -> studied,
data -> cases, design -> procedure, measures -> lens) and `schema_version` is set to the current version. What needs judgement
is listed, not guessed: definitions named in a setup that need a `kind` (rule 63), and claims that need an
`about` (rule 64 for principal claims, warning 27 for the others).
"""

from .validate import OLD_SETUP_ROLES, SCHEMA_VERSION


def _is_assumption(claim):
    return any(isinstance(s, dict) and s.get("kind") == "assumption" for s in claim.get("support") or [])


def migrate(doc):
    """(migrated copy, list of changes made, list of things left to do)."""
    import copy
    out = copy.deepcopy(doc)
    done, todo = [], []
    if out.get("schema_version") != SCHEMA_VERSION:
        done.append(f"schema_version {out.get('schema_version')} -> {SCHEMA_VERSION}")
        out["schema_version"] = SCHEMA_VERSION
    framed = set()
    for e in out.get("evidence") or []:
        setup = e.get("setup") if isinstance(e, dict) else None
        if not isinstance(setup, dict):
            continue
        renamed = {}
        for role, ids in setup.items():
            new = OLD_SETUP_ROLES.get(role, role)
            if new != role:
                done.append(f"{e.get('id')}.setup: {role} -> {new}")
            renamed.setdefault(new, [])
            renamed[new] += [i for i in (ids if isinstance(ids, list) else [ids]) if i not in renamed[new]]
        e["setup"] = renamed
        for ids in renamed.values():
            framed |= {i for i in ids if isinstance(i, str)}
    for d in out.get("definitions") or []:
        if isinstance(d, dict) and d.get("id") in framed and "kind" not in d:
            todo.append(f"{d['id']} ({d.get('term')}): named in a setup; give it a kind (rule 63)")
    for c in out.get("claims") or []:
        if not isinstance(c, dict) or "about" in c or _is_assumption(c):
            continue
        need = "rule 64" if c.get("role") == "principal" else "warning 27"
        todo.append(f"{c.get('id')}: give its about, the atom of its statement ({need})")
    return out, done, todo
