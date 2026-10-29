"""Bringing a claimset written under an earlier version (v0.23 or later) to the current version.

Only what is mechanical is changed:
- v0.30: the roles of each evidence `setup` are renamed (compared -> studied, data -> cases, design -> procedure,
  measures -> lens);
- v0.32: the kind `measure` is renamed `variable`; a result's `quantity` is renamed `variable`;
- `schema_version` is set to the current version.
From v0.33 a claim inherits the assumptions of the methods studied only for its own subject (§3.2): the
assumptions a claim inherited under v0.32 and no longer does are listed, to be added to its own `assumptions`
where it does rest on them.
What needs judgement is listed, not guessed: definitions without a `kind` (rule 63), claims that need an `about`
(rule 64 for principal claims, warning 27 for the others), figures that need a `function` (rule 67), evidence without a setup (rule 61) and evidence or artefacts
without an availability (rule 75), the
`exemplars` of qualitative evidence, which become excerpt results (rule 66), and declarations (warning 29).
"""

from .validate import OLD_SETUP_ROLES, SCHEMA_VERSION, as_dict, as_list, inherited_assumptions


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
    for e in out.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        setup = e.get("setup")
        if isinstance(setup, dict):
            renamed = {}
            for role, ids in setup.items():
                new = OLD_SETUP_ROLES.get(role, role)
                if new != role:
                    done.append(f"{e.get('id')}.setup: {role} -> {new}")
                renamed.setdefault(new, [])
                renamed[new] += [i for i in (ids if isinstance(ids, list) else [ids]) if i not in renamed[new]]
            e["setup"] = renamed
        for rs in e.get("results") or []:
            if isinstance(rs, dict) and "quantity" in rs and "variable" not in rs:
                rs_items = list(rs.items())
                rs.clear()
                rs.update({("variable" if k == "quantity" else k): v for k, v in rs_items})
                done.append(f"{e.get('id')}.{rs.get('id')}: quantity -> variable")
        if "exemplars" in e:
            todo.append(f"{e.get('id')}: record each exemplar as a result whose value is the excerpt "
                        f"{{text, locator}}, then remove exemplars (rule 66)")
    for coll in ("evidence", "artefacts"):
        for x in out.get(coll) or []:
            if isinstance(x, dict) and "availability" not in x:
                todo.append(f"{x.get('id')}: give its availability; not_stated when the paper does not say (rule 75)")
            if coll == "evidence" and isinstance(x, dict) and x.get("profile") != "formal" and not x.get("setup"):
                todo.append(f"{x.get('id')}: give its setup, the parts of the study by role (rule 61)")
    for d in out.get("definitions") or []:
        if not isinstance(d, dict):
            continue
        if d.get("kind") == "measure":
            d["kind"] = "variable"
            done.append(f"{d.get('id')}: kind measure -> variable")
        if "kind" not in d:
            todo.append(f"{d['id']} ({d.get('term')}): give it a kind, what sort of thing the term names (rule 63)")
    if str(doc.get("schema_version")) in ("0.32",):
        defs = {d.get("id"): d for d in out.get("definitions") or [] if isinstance(d, dict)}
        evid = {e.get("id"): e for e in out.get("evidence") or [] if isinstance(e, dict)}
        for c in out.get("claims") or []:
            if not isinstance(c, dict):
                continue
            before = inherited_assumptions({k: v for k, v in c.items() if k != "about"}, defs, evid)
            now = set(inherited_assumptions(c, defs, evid)) | set(as_list(c.get("assumptions")))
            lost = [a for a in before if a not in now]
            if lost:
                todo.append(f"{c.get('id')}: no longer inherits {', '.join(lost)}, the assumptions of a method it is "
                            f"not about (§3.2); list in its assumptions those it rests on")
    for c in out.get("claims") or []:
        if not isinstance(c, dict) or "about" in c or _is_assumption(c):
            continue
        need = "rule 64" if c.get("role") == "principal" else "warning 27"
        todo.append(f"{c.get('id')}: give its about, the atom of its statement ({need})")
    for a in out.get("artefacts") or []:
        if isinstance(a, dict) and a.get("kind") == "figure" and "function" not in a:
            todo.append(f"{a.get('id')}: give the figure its function, data_plot, specimen or diagram, and for a "
                        f"data plot the observations it shows (rule 67)")
    paper = out.get("paper") if isinstance(out.get("paper"), dict) else {}
    if paper.get("origin") == "native" and "declarations" not in paper:
        todo.append("paper.declarations: ask the guarantor for funding and competing interests, and record ethics "
                    "and consent where people took part (warning 29)")
    return out, done, todo
