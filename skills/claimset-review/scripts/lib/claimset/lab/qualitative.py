"""The lab record part of the qualitative extension (spec/extensions/qualitative-v0.1.md §2): codes of a codebook,
codings, the codings runs read, counts of coded material and the flags of tests over them.

The lab core calls this module only for records that declare the extension: records begun under lab record
v0.20 or later that wrote its `extension` event, and every record begun earlier, which has it from its start and
keeps the forms written then (a term's `code`, a run's `codings`, count quantities in a decision rule).
"""

import json

from .core import LabError, State, _ref, _req, file_hash, is_withheld
from .sources import is_private, source_dir, text_of

NAME = "qualitative"
#: The first lab record version in which the extension's fields are in its blocks (`ext.qualitative`).
BLOCKS_VERSION = (0, 20)


def _rule(state, old, new):
    """The rule's number: the core's in records begun before v0.20, the extension's from then."""
    return old if state.spec_version is not None and state.spec_version < BLOCKS_VERSION else new


def block_of(body):
    """The extension's block on an event body ({} if none)."""
    ext = body.get("ext") if isinstance(body, dict) else None
    b = ext.get(NAME) if isinstance(ext, dict) else None
    return b if isinstance(b, dict) else {}


def code_of(term_body):
    """A term's code criteria {include, exclude, examples}: its block's, or its `code` in older records."""
    if not isinstance(term_body, dict):
        return None
    code = block_of(term_body).get("code")
    return code if code is not None else term_body.get("code")


def run_codings(run_body):
    """The codings file a run read {path, hash, count}: its block's, or its `codings` in older records."""
    if not isinstance(run_body, dict):
        return None
    c = block_of(run_body).get("codings")
    return c if c is not None else run_body.get("codings")


def check_code(code, kind):
    """A code's criteria on a term of kind concept (§2.1, rule qualitative.L4)."""
    from ..extensions.qualitative_v0_1 import code_problems
    if kind != "concept":
        raise LabError("schema", "term: a code (a term with a code part) is a concept")
    probs = code_problems(code)
    if probs:
        raise LabError("schema", "term: " + "; ".join(probs))


def check_block(state, etype, body, block, eid):
    """The extension's block on a term, plan or run (rule qualitative.L4)."""
    allowed = {"term": {"code"}, "plan": {"counts"}, "run": {"codings"}}[etype]
    extra = set(block) - allowed
    if extra:
        raise LabError("qualitative.L4", f"{etype}: the qualitative block has only {', '.join(sorted(allowed))}, "
                                         f"not {', '.join(sorted(extra))}")
    if etype == "term" and "code" in block:
        check_code(block["code"], body.get("kind"))
    if etype == "plan" and "counts" in block:
        check_counts(state, block["counts"], body.get("decision"))
    if etype == "run" and "codings" in block:
        c = block["codings"]
        if not (isinstance(c, dict) and isinstance(c.get("path"), str) and isinstance(c.get("hash"), str)
                and isinstance(c.get("count"), int)):
            raise LabError("qualitative.L4", "run: codings is {path, hash, count}, written by the tool")


def check_counts(state, counts, decision):
    """A plan's counts (§2.4, rule qualitative.L3): well formed, naming codes, each bound by a quantity."""
    from ..extensions.qualitative_count import result_variable, spec_problems
    if not isinstance(counts, dict) or not counts:
        raise LabError("qualitative.L3", "plan: counts is an object {name: count}")
    quantities = decision.get("quantities") if isinstance(decision, dict) else None
    for name, spec in counts.items():
        probs = spec_problems(name, spec)
        if probs:
            raise LabError("qualitative.L3", "plan: " + "; ".join(probs))
        for c in spec.get("codes") or []:
            if state.get(c) is None or not is_code(state, c):
                raise LabError("qualitative.L3", f"plan: count {name} names {c}, which is not a code")
        q = quantities.get(name) if isinstance(quantities, dict) else None
        if not (isinstance(q, dict) and q.get("variable") == result_variable(name)):
            raise LabError("qualitative.L3", f"plan: count {name} is bound by a quantity of the decision rule, "
                                             f"{name}: {{\"variable\": \"{result_variable(name)}\"}}")


def check_event(state, etype, body, by, eid):
    """The extension's own event types (§2.2)."""
    if etype == "coding":
        check_coding(state, body, by, eid)


def plan_flags(state, plan_version, runs):
    """The extension's flags of a decided plan (§1.2, §2.5)."""
    out = []
    if coded_before_plan(state, plan_version, runs):
        out.append("coded_before_plan")
    if codebook_changed(state, plan_version, runs):
        out.append("codebook_changed_after_plan")
    return out


def prepare_run(lab, st, rdir, env):
    """Before a run: the codings in force, in `codings.json`, named in $CLAIMSET_CODINGS (§2.3). Returns the
    block for the run, {codings: {path, hash, count}}, or None when the record has no codings."""
    snapshot = codings_snapshot(st)
    if snapshot is None:
        return None
    (rdir / CODINGS_FILE).write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    env["CLAIMSET_CODINGS"] = str((rdir / CODINGS_FILE).resolve())
    return {"codings": {"path": CODINGS_FILE, "hash": file_hash(rdir / CODINGS_FILE),
                        "count": len(snapshot["codings"])}}


def verify(lab, events):
    """Checks that need the lab's files: the quotes of codings (qualitative.L2)."""
    return verify_codings(lab, events)


def is_code(state, term_id):
    """Whether a term ID names a code: a term whose latest version has a code part (§2.1)."""
    e = state.latest(term_id) if isinstance(term_id, str) and state.get(term_id) else None
    return e is not None and e["type"] == "term" and not is_withheld(e) and isinstance(code_of(e["body"]), dict)


SELECTOR_KEYS = ("exact", "prefix", "suffix", "start", "end")


def check_coding(state, body, by, eid):
    """A coding (§2.2): codes applied to a captured source, or to a passage of it, by its writer (qualitative.L1;
    L27 in records begun before lab record v0.20)."""
    sid = _ref(state, _req(body, "source", "coding"), "coding", "source", {"source"})
    empty = body.get("withdrawn") is True or body.get("none_apply") is True
    codes = body.get("codes", [] if empty else None)
    if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
        raise LabError("schema", "coding: codes must be a list of term IDs")
    if body.get("withdrawn") is True and body.get("none_apply") is True:
        raise LabError("schema", "coding: withdrawn or none_apply, not both")
    if empty and codes:
        raise LabError("schema", "coding: a coding that is withdrawn, or that says no code applies, names no codes")
    if not codes and not empty:
        raise LabError("schema", "coding: name at least one code; a source read and given no code says none_apply: "
                                 "true (`--none`), a correction that removes the codes says withdrawn: true")
    if body.get("none_apply") is True and body.get("quote") is not None:
        raise LabError("schema", "coding: a coding that says no code applies is of the whole source, with no quote")
    for c in codes:
        if state.get(c) is None or not is_code(state, c):
            raise LabError(_rule(state, "L27", "qualitative.L1"), f"coding: {c} is not a code: record it as a term of kind concept with "
                                  f"--include (and --exclude) first")
    if len(set(state.root(c) for c in codes)) != len(codes):
        raise LabError("schema", "coding: a code is named once")
    versions = body.get("code_versions")
    if versions is not None:
        if not isinstance(versions, dict) or set(versions) != set(codes):
            raise LabError("schema", "coding: code_versions has one entry per code: the version applied")
        for c, v in versions.items():
            if not isinstance(v, str) or state.get(v) is None or state.root(v) != state.root(c):
                raise LabError("L12", f"coding: {v!r} is not a version of {c}")
    sel = body.get("quote")
    if sel is not None:
        src = state.get(sid)
        if not isinstance(sel, dict) or not isinstance(sel.get("exact"), str) or not sel["exact"].strip() \
                or not all(isinstance(sel.get(k), int) and not isinstance(sel.get(k), bool) for k in ("start", "end")) \
                or not 0 <= sel["start"] < sel["end"] or set(sel) - set(SELECTOR_KEYS):
            raise LabError("schema", "coding: quote is {exact, prefix, suffix, start, end}, as `claimset lab quote` "
                                     "gives it")
        if src is not None and not is_withheld(src) and \
                not any(isinstance(f, dict) and f.get("role") == "text" for f in src["body"].get("files") or []) \
                and src["body"].get("format") not in ("text/plain", "text/markdown", "text/csv", "application/json",
                                                       "text/html", "application/xhtml+xml"):
            raise LabError("L25", f"coding: {sid} has no text to quote from")
    for f in ("unit", "pass", "memo"):
        v = body.get(f)
        if v is not None and (not isinstance(v, (str, int)) or isinstance(v, bool) or (isinstance(v, str) and not v.strip())):
            raise LabError("schema", f"coding: {f} must be non-empty text or a number")



def count_specs(plan_body):
    """{name: count specification} of a plan (§2.4): its block's counts, or, in records begun before lab record
    v0.20, its decision rule's count quantities."""
    if not isinstance(plan_body, dict):
        return {}
    block = block_of(plan_body)
    if isinstance(block.get("counts"), dict):
        return {n: v for n, v in block["counts"].items() if isinstance(v, dict)}
    decision = plan_body.get("decision")
    q = decision.get("quantities") if isinstance(decision, dict) else None
    return {n: v["count"] for n, v in (q or {}).items() if isinstance(v, dict) and isinstance(v.get("count"), dict)}


def plan_codes(state, plan_version):
    """The roots of the codes a plan's counts name, with every narrower code (whose broader chain reaches them).
    None if the plan counts no codes."""
    named = {state.root(c) for spec in count_specs(plan_version["body"]).values()
             for c in spec.get("codes") or [] if isinstance(c, str) and state.get(c)}
    if not named:
        return None
    out = set(named)
    grew = True
    while grew:
        grew = False
        for e in state.events:
            if e.get("type") != "term" or state.root(e["id"]) in out:
                continue
            cur = state.latest(e["id"])
            if cur and {state.root(b) for b in cur["body"].get("broader") or [] if state.get(b)} & out:
                out.add(state.root(e["id"]))
                grew = True
    return out


def codebook_changed(state, plan_version, runs):
    """Codes the plan counts (or, if it counts none, codes applied by codings its deciding runs read) whose term
    was corrected after the plan and before a deciding run (§2.5): the codebook the test used is not the one the
    plan fixed. Returns their root IDs."""
    if not any(isinstance(run_codings(r["body"]), dict) for r in runs if not is_withheld(r)):
        return []
    codes = plan_codes(state, plan_version)
    if codes is None:
        codes = {state.root(c) for e in state.events if e.get("type") == "coding"
                 for c in e["body"].get("codes") or [] if isinstance(c, str) and state.get(c)}
    start = state.pos[plan_version["id"]]
    end = max(state.pos.get(r["id"], 0) for r in runs)
    out = []
    for e in state.events:
        if e.get("type") == "term" and e.get("supersedes") and state.root(e["id"]) in codes \
                and start < state.pos.get(e["id"], 0) < end and state.root(e["id"]) not in out:
            out.append(state.root(e["id"]))
    return out


def coded_before_plan(state, plan_version, runs):
    """Codings written before the plan that its deciding runs read, of the codes the plan counts (or of any code,
    if it counts none) (§2.5): the material was coded before the test was fixed. Returns their root IDs."""
    if not any(isinstance(run_codings(r["body"]), dict) for r in runs if not is_withheld(r)):
        return []
    codes = plan_codes(state, plan_version)
    start = state.pos[plan_version["id"]]
    end = max(state.pos.get(r["id"], 0) for r in runs)
    out = []
    for e in state.events:
        if e.get("type") != "coding" or e.get("supersedes") or state.pos.get(e["id"], 0) >= start:
            continue
        latest = None
        for v in [e] + [x for x in state.events if x.get("type") == "coding" and state.root(x["id"]) == e["id"]]:
            if state.pos.get(v["id"], 0) < end:
                latest = v
        if latest is None or is_withheld(latest) or latest["body"].get("withdrawn") or not latest["body"].get("codes"):
            continue
        applied = {state.root(c) for c in latest["body"]["codes"] if isinstance(c, str) and state.get(c)}
        if codes is None or applied & codes:
            out.append(e["id"])
    return out



CODINGS_FILE = "codings.json"


def verify_codings(lab, events):
    """Rule qualitative.L2 (L25 in records begun before lab record v0.20): every coding's quote is in its source's
    text, at the recorded place."""
    st = State()
    for e in events:
        if isinstance(e, dict):
            st.apply(e)
    errors = []
    rule = _rule(st, "L25", "qualitative.L2")
    for e in events:
        if not isinstance(e, dict) or e.get("type") != "coding" or is_withheld(e) or not isinstance(e.get("body"), dict):
            continue
        sel = e["body"].get("quote")
        src = st.get(e["body"].get("source"))
        if not isinstance(sel, dict) or src is None or is_withheld(src):
            continue
        if is_private(src.get("body")) and not source_dir(lab, src).is_dir():
            continue                              # kept out of git; source_notes says so
        try:
            text = text_of(lab, src)
        except (LabError, OSError):
            errors.append({"seq": e["seq"], "id": e["id"], "rule": rule,
                           "message": f"the text of {src['id']} cannot be read, so the quote cannot be checked"})
            continue
        start, end = sel.get("start"), sel.get("end")
        if not (isinstance(start, int) and isinstance(end, int) and text[start:end] == sel.get("exact")):
            errors.append({"seq": e["seq"], "id": e["id"], "rule": rule,
                           "message": f"the quote is not in {src['id']}'s text at {start}-{end}"})
    return errors


def current_codings(st):
    """The codings in force in a view of the record: the latest version of each, without withdrawn or withheld
    ones, in record order."""
    out = []
    for e in st.events:
        if e.get("type") != "coding" or st.root(e["id"]) != e["id"]:
            continue
        cur = st.latest(e["id"])
        if cur is None or is_withheld(cur) or cur["body"].get("withdrawn") or \
                not (cur["body"].get("codes") or cur["body"].get("none_apply")):
            continue
        out.append(cur)
    return out


def codings_snapshot(st):
    """What a run reads in $CLAIMSET_CODINGS (§2.3): the codebook, the sources coded and the codings in force,
    as the run's writer sees them. None if the view has no coding."""
    codings = current_codings(st)
    if not codings:
        return None
    codes, sources = {}, {}
    for c in codings:
        for t in c["body"]["codes"]:
            term = st.latest(t)
            root = st.root(t)
            if root not in codes and term is not None and not is_withheld(term):
                tb = term["body"]
                codes[root] = {"id": root, "version": term["id"], "term": tb.get("term"),
                               "definition": tb.get("definition"), "code": code_of(tb),
                               "broader": [st.root(b) for b in tb.get("broader") or []]}
        s = st.get(c["body"]["source"])
        if s is not None and not is_withheld(s) and s["id"] not in sources:
            b = s["body"]
            sources[s["id"]] = {k: b.get(k) for k in ("uri", "title", "author", "venue", "published_at",
                                                      "retrieved_at") if b.get(k) is not None}
            sources[s["id"]]["id"] = s["id"]
    rows = []
    for c in codings:
        b = c["body"]
        row = {"id": c["id"], "root": st.root(c["id"]), "source": b["source"],
               "codes": [st.root(t) for t in b["codes"]], "code_versions": b.get("code_versions"),
               "coder": c.get("by"), "at": c.get("at")}
        for k in ("unit", "pass", "memo"):
            if b.get(k) is not None:
                row[k] = b[k]
        if b.get("none_apply"):
            row["none_apply"] = True
        if isinstance(b.get("quote"), dict):
            row["quote"] = {k: b["quote"][k] for k in ("exact", "start", "end") if k in b["quote"]}
        rows.append(row)
    return {"codes": list(codes.values()), "sources": list(sources.values()), "codings": rows}
