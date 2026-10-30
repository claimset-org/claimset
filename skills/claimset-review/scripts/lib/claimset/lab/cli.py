"""`claimset lab ...` commands (lab record spec §10)."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .core import (Lab, LabError, duplicate_runs, file_hash, plan_flags, plan_warnings, uncounted_exclusions,
                   verify)
from . import summary as _summary
from .run import run_command
from . import export as _export
from . import decision as _decision
from . import anchor as _anchor
from . import guard as _guard
from . import protect as _protect
from . import layout as _layout
from . import record as _record

#: Commands that only read the record: they are allowed anywhere and never commit.
READERS = {"summary", "list", "show", "verify", "verdict", "guard", "studies"}

def _lab(args):
    return Lab(_layout.find_lab(args.lab, getattr(args, "study", None)))


def _actor(args, required=True):
    by = args.by or os.environ.get("CLAIMSET_ACTOR")
    if required and not by:
        raise LabError("L8", "say who is writing: --by ACTOR or the CLAIMSET_ACTOR environment variable")
    return by


def _clean(body):
    return {k: v for k, v in body.items() if v is not None and v != []}


def _emit(args, event):
    if getattr(args, "json", False):
        print(json.dumps(event, ensure_ascii=False, indent=2))
    else:
        print(event["id"])


def _auto_anchor(lab, event):
    """Anchor after a plan (or after every event, if configured); warn instead of failing. A lab on its study
    branch is committed and pushed after the command instead (see run())."""
    cfg = _anchor.anchoring(lab)
    if not cfg or _record.config(lab):
        return
    if event["type"] == "plan" or cfg.get("every") == "event":
        try:
            entry = _anchor.anchor(lab)
            print(f"claimset: anchored seq {entry['seq']} at {entry['receipt']['commit'][:12]}", file=sys.stderr)
        except LabError as exc:
            print(f"claimset: WARNING {event['id']} was written but not anchored: {exc.message}", file=sys.stderr)


def _write(args, etype, body, by=None):
    lab = _lab(args)
    by = _actor(args) if by is None else by
    event = lab.append(etype, _clean(body), by=by, supersedes=getattr(args, "supersedes", None))
    _emit(args, event)
    _auto_anchor(lab, event)
    return 0


# ---------------------------------------------------------------- handlers

def cmd_init(args):
    explicit = args.lab or os.environ.get("CLAIMSET_LAB")
    if not explicit and args.study and _layout.repo_top() is not None:
        # in a git repository a study lives on its own branch (§3)
        lab, what, notes = _record.setup(args.study, title=args.title, repository=args.repository)
        print(f"INIT {lab.log_path} (on branch {_record.PREFIX}{args.study})")
        for n in notes:
            print(n)
        return 0
    if explicit:
        root = Path(explicit)
    elif args.study:
        root = _layout.study_dir(args.study) / "lab"
    else:
        raise LabError("layout", "name the study: `claimset lab init --study NAME --title …` creates "
                                 "claimset/NAME/lab (or give the directory with --lab)")
    if args.study:
        _layout.check_name(args.study, "study")
    lab = Lab.create(root, args.title, args.repository, study=args.study)
    print(f"INIT {lab.log_path}")
    top = _layout.studies_top(lab.root)
    if top is not None:
        for name, line in _layout.add_ignores(top):
            print(f"added '{line}' to {name}, so package builds leave the studies out")
    return 0


def cmd_paper(args):
    lab = _lab(args)
    body = {"name": _layout.check_name(args.name, "paper"), "questions": args.questions, "title": args.title}
    event = lab.append("paper", _clean(body), _actor(args), args.supersedes)
    _emit(args, event)
    return 0


def _rel(path, top):
    try:
        return Path(path).resolve().relative_to(top.resolve()).as_posix()
    except ValueError:
        return str(Path(path).resolve())


def cmd_move(args):
    """Move the lab (and files the record names) to a new place, and record the move (§3.4)."""
    import shutil

    lab = _lab(args)
    st = lab.state()
    if any(a not in st.merged and lab.read_agent(a) for a in lab.agent_logs()):
        raise LabError("move", "merge the open agent logs before moving the lab")
    study = args.study or st.study
    if args.to:
        dest = Path(args.to)
    elif study:
        dest = _layout.study_dir(study, lab.root) / "lab"
    else:
        raise LabError("move", "give the new place with --to, or the study's name with --study")
    if dest.exists() and any(dest.iterdir()):
        raise LabError("move", f"{dest} exists and is not empty")
    top = _layout.repo_top(lab.root) or Path.cwd()
    moves = []
    for spec in args.also:
        if "=" not in spec:
            raise LabError("move", f"--also '{spec}': use OLD=NEW")
        old, new = spec.split("=", 1)
        if not Path(old).exists():
            raise LabError("move", f"--also: {old} does not exist")
        if Path(new).exists():
            raise LabError("move", f"--also: {new} exists")
        moves.append((Path(old), Path(new)))
    absolute = lambda x: x if x.is_absolute() else Path.cwd() / x
    body = {"from": _rel(lab.root, top), "to": _rel(absolute(dest), top),
            "study": study if study != st.study else None,
            "moved": [{"from": _rel(o, top), "to": _rel(absolute(n), top)}
                      for o, n in moves] or None}
    body = _clean(body)
    by = _actor(args)
    st.check("relocation", st.next_id("relocation"), by, body, None)    # refuse before moving anything
    cfg = _anchor.load_config(lab)
    if cfg.get("anchor") and not cfg["anchor"].get("branch"):
        cfg["anchor"]["branch"] = _anchor.default_branch(lab)      # keep the branch the anchors are on
        _anchor.save_config(lab, cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.rmdir()
    shutil.move(str(lab.root), str(dest))
    done = []
    try:
        for old, new in moves:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
            done.append((old, new))
        moved = Lab(dest, clock=lab.clock)
        event = moved.append("relocation", body, by)
    except Exception:
        for old, new in reversed(done):
            shutil.move(str(new), str(old))
        shutil.move(str(dest), str(lab.root))
        raise
    _emit(args, event)
    print(f"moved {body['from']} to {body['to']}; commit the move (git add -A) so history follows it",
          file=sys.stderr)
    t = _layout.studies_top(dest)
    if t is not None:
        for name, line in _layout.add_ignores(t):
            print(f"added '{line}' to {name}, so package builds leave the studies out", file=sys.stderr)
    return 0


def _parse_model(spec):
    parts = [p.strip() for p in spec.split("|")]
    if len(parts) < 3:
        raise LabError("schema", f"--model '{spec}': use 'provider|name|version[|identifier]'")
    m = {"provider": parts[0], "name": parts[1], "version": parts[2]}
    if len(parts) > 3 and parts[3]:
        m["identifier"] = parts[3]
    return m


def cmd_actor(args):
    lab = _lab(args)
    st = lab.state()
    new_id = st.next_id("actor")
    by = _actor(args, required=False) or new_id
    body = {"kind": args.kind, "name": args.name, "orcid": args.orcid,
            "models": [_parse_model(m) for m in args.model] or None,
            "record_source": args.record_source}
    event = lab.append("actor", _clean(body), by=by, supersedes=args.supersedes)
    _emit(args, event)
    return 0


def cmd_question(args):
    return _write(args, "question", {"text": args.text})


def _about_from_args(args):
    """A hypothesis's `about` from --subject, --relation, --object, --comparator and --condition, or None."""
    given = [args.subject, args.relation, args.object, args.comparator, args.condition]
    if not any(given):
        return None
    if not args.subject or not args.relation:
        raise LabError("schema", "about: give at least --subject and --relation (and --object, --comparator, "
                                 "--condition where they apply)")
    obj = args.object or []
    return {"subject": args.subject, "relation": args.relation,
            "object": None if not obj else (obj[0] if len(obj) == 1 else obj),
            "comparator": args.comparator, "conditions": list(args.condition or [])}


def cmd_term(args):
    return _write(args, "term", {"term": args.term, "kind": args.kind, "definition": args.definition,
                                 "source": args.source})


def cmd_hypothesis(args):
    body = {"statement": args.statement, "question": args.question,
            "origin": "from_exploration" if args.from_exploration else "a_priori",
            "prompted_by": args.prompted_by}
    about = _about_from_args(args)
    if about is not None:
        body["about"] = about
    return _write(args, "hypothesis", body)


def cmd_assume(args):
    return _write(args, "assumption", {"statement": args.statement, "category": args.category,
                                       "check": args.check, "check_evidence": args.check_evidence})


def cmd_note(args):
    return _write(args, "note", {"text": args.text, "refers_to": args.refers_to})


def cmd_decide(args):
    return _write(args, "decision", {"text": args.text, "prompted_by": args.prompted_by,
                                     "affects": args.affects})


def cmd_status(args):
    return _write(args, "status", {"target": args.target, "to": args.to, "rationale": args.why,
                                   "evidence": args.evidence, "revised_to": args.revised_to,
                                   "plan": args.plan, "rule": args.rule})


def cmd_check(args):
    return _write(args, "check", {"target": args.target, "outcome": args.outcome, "extent": args.extent,
                                  "notes": args.notes, "resolution": args.resolution})


def cmd_artefact(args):
    h = args.hash
    p = Path(args.path_or_uri)
    if h is None and p.is_file():
        h = file_hash(p)
    return _write(args, "artefact", {"kind": args.kind, "path_or_uri": args.path_or_uri, "hash": h,
                                     "licence": args.licence, "description": args.description})


def _json_or_text(v):
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def _resolution_test(comment, reviews):
    """The current resolution test of a review comment, from review and re-review files (revision spec §6.3): the
    latest replacement or accepted test given in a verdict, or the comment's own test."""
    m = re.match(r"^(\S+@v[1-9]\d*)/(RV[1-9]\d*)\.(c[1-9]\d*)$", comment)
    if not m:
        raise LabError("schema", f"--comment {comment}: give the comment's global ID, <claimset>@v<n>/RV<k>.c<m>")
    test = None
    for path in reviews:
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LabError("schema", f"--review {path}: {exc}") from None
        if not isinstance(doc, dict):
            raise LabError("schema", f"--review {path}: not a review")
        target = (doc.get("target") or {}).get("version") if isinstance(doc.get("target"), dict) else None
        if doc.get("id") == m.group(2) and target == m.group(1):
            for c in doc.get("comments") or []:
                if isinstance(c, dict) and c.get("id") == m.group(3) and test is None:
                    test = c.get("resolution_test")
        for v in doc.get("verdicts") or []:
            if isinstance(v, dict) and v.get("comment") == comment:
                test = v.get("accepted_test") or v.get("replacement_test") or test
    if not isinstance(test, dict):
        raise LabError("schema", f"--comment {comment}: no resolution test for it in "
                                 f"{', '.join(reviews) or 'the review files given (--review)'}")
    if test.get("framework") == "documentary" or not test.get("quantities"):
        raise LabError("schema", f"--comment {comment}: its resolution test is decided by text, not computed from "
                                 f"results; answer it in the response rather than with a planned test")
    return test


def _decision_from_args(args):
    """The decision rule from --decision FILE, overridden by the individual options."""
    d = {}
    if args.decision:
        try:
            d = json.loads(Path(args.decision).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LabError("schema", f"--decision {args.decision}: {exc}") from None
        if not isinstance(d, dict):
            raise LabError("schema", f"--decision {args.decision} must hold a JSON object")
    if args.framework:
        d["framework"] = args.framework
    for p in args.parameter:
        k, v = _parse_param(p)
        d.setdefault("parameters", {})[k] = v
    for q in args.quantity:
        if "=" not in q:
            raise LabError("schema", f"--quantity '{q}': use name=\"quantity text\" or name='{{json}}'")
        name, spec = q.split("=", 1)
        spec = _json_or_text(spec)
        d.setdefault("quantities", {})[name] = spec if isinstance(spec, dict) else {"quantity": str(spec)}
    if args.supported is not None:
        d["supported"] = args.supported
    if args.not_supported is not None:
        d["not_supported"] = args.not_supported
    return d or None


def cmd_plan(args):
    from .run import code_version

    data = [{"description": d, "hash": file_hash(d)} if Path(d).is_file() else {"description": d}
            for d in args.data]
    holdout = []
    for h in args.holdout:
        if not Path(h).is_file():
            raise LabError("schema", f"--holdout {h}: no such file")
        holdout.append({"path": h, "hash": file_hash(h)})
    addresses = list(args.addresses or [])
    decision = _decision_from_args(args)
    design = args.design
    if args.comment:
        if decision is not None:
            raise LabError("schema", "--comment takes the decision rule from the comment's resolution test; leave out "
                                     "--framework, --parameter, --quantity, --supported, --not-supported and --decision")
        test = _resolution_test(args.comment, args.review)
        decision = {"framework": test["framework"], "parameters": test.get("parameters") or {},
                    "quantities": test["quantities"], "supported": test.get("withdrawn"),
                    "not_supported": test.get("upheld")}
        design = design or test.get("procedure")
        addresses = [args.comment] + [a for a in addresses if a != args.comment]
    if not design:
        raise LabError("schema", "plan: --design is required (with --comment it defaults to the test's procedure)")
    exclusions = []
    for x in args.exclusion:
        if "=" not in x:
            raise LabError("schema", f"--exclusion '{x}': use name=\"rule\", e.g. edge=\"models within 1e-8 of the "
                                     f"boundary\"")
        name, rule = x.split("=", 1)
        exclusions.append({"name": name.strip(), "rule": rule.strip()})
    commit, dirty = code_version(os.getcwd(), _lab(args).root)
    setup = {role: list(getattr(args, role)) for role in ("studied", "cases", "procedure", "lens", "context")
             if getattr(args, role)}
    body = {"kind": args.kind, "hypotheses": args.hypotheses, "design": design, "analysis": args.analysis,
            "setup": setup or None,
            "new_data": args.new_data, "exclusions": exclusions or None, "addresses": addresses or None,
            "decision": decision, "requires_approval": True if args.requires_approval else None,
            "runs": args.runs, "criterion": args.criterion, "data": data, "holdout": holdout,
            "code_version": commit, "dirty": dirty if commit is not None else None}
    warnings = plan_warnings(_view(args), body)
    code = _write(args, "plan", body)
    for w in warnings:
        print(f"claimset: warning: {w}", file=sys.stderr)
    return code


def cmd_verdict(args):
    st = _view(args)
    plan = st.get(args.plan)
    if plan is None or plan["type"] != "plan":
        raise LabError("L12", f"no plan '{args.plan}'")
    d = st.deciding_runs(args.plan)
    pv = st.plan_rule_version(args.plan)
    if pv is None or "decision" not in pv["body"]:
        raise LabError("schema", f"{args.plan} has no decision rule")
    rule = pv["body"]["decision"]
    runs = st.plan_runs.get(st.root(args.plan), [])
    out = {"plan": pv["id"], "framework": rule.get("framework"), "decided": bool(d),
           "deciding_runs": [r["id"] for r in d[1]] if d else [r["id"] for r in runs],
           "runs_needed": pv["body"].get("runs", 1), "supported": rule.get("supported"),
           "not_supported": rule.get("not_supported")}
    if d:
        out["flags"] = plan_flags(st, d[0], d[1])
        out["warnings"] = [f"exclusion '{x}': no deciding run reports how many units it excluded (a result whose "
                           f"quantity contains \"excluded\" or the exclusion's name)"
                           for x in uncounted_exclusions(st, d[0], d[1])]
        if _decision.computed(rule):
            out.update(_decision.verdict(rule, [[x["body"] for x in st.results_of.get(r["id"], [])] for r in d[1]]))
        else:
            out["verdict"] = None
            out["note"] = "text rules: judge which rule the evidence meets and record it with --rule"
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"{out['plan']} ({out['framework']}): supported if {out['supported']}; "
          f"not_supported if {out['not_supported']}; otherwise inconclusive")
    if not d:
        print(f"Not decided yet: {len(runs)} of {out['runs_needed']} deciding runs "
              f"({', '.join(out['deciding_runs']) or 'none'}).")
        return 0
    print(f"Deciding runs: {', '.join(out['deciding_runs'])}")
    for k, v in (out.get("values") or {}).items():
        print(f"  {k} = {v}")
    for p in out.get("problems") or []:
        print(f"  problem: {p}")
    print(f"Verdict: {out['verdict']}" if out.get("verdict") else out["note"])
    for f in out.get("flags") or []:
        print(f"  flag: {f.replace('_', ' ')}")
    for w in out.get("warnings") or []:
        print(f"  warning: {w}")
    return 0


def cmd_approve(args):
    return _write(args, "approval", {"target": args.target, "comment": args.comment,
                                     "on_behalf_of": args.on_behalf_of, "words": args.words,
                                     "given_at": args.given_at, "channel": args.channel})


def cmd_deviate(args):
    return _write(args, "deviation", {"plan": args.plan, "run": args.run, "text": args.text,
                                      "reason": args.reason})


def _parse_param(p):
    if "=" not in p:
        raise LabError("schema", f"--param '{p}': use name=value")
    k, v = p.split("=", 1)
    try:
        v = json.loads(v)
    except json.JSONDecodeError:
        pass
    return k, v


def cmd_run(args):
    argv = list(args.command)
    if argv and argv[0] == "--":
        argv = argv[1:]
    lab = _lab(args)
    out = run_command(lab, _actor(args), argv, plan=args.plan, exploratory=args.exploratory,
                      parameters=dict(_parse_param(p) for p in args.param) or None,
                      seeds=args.seed or None, data=args.data or None)
    run = out["run"]
    if args.json:
        print(json.dumps({"run": run, "results": out["results"], "problems": out["problems"]},
                         ensure_ascii=False, indent=2))
    msg = (f"claimset: recorded {run['id']} ({run['body']['label']}, {run['body']['exit_status']}, "
           f"exit code {out['exit_code']})")
    if out["results"]:
        msg += "; results " + " ".join(r["id"] for r in out["results"])
    print(msg, file=sys.stderr)
    for p in out["problems"]:
        print(f"claimset: results.json {p}", file=sys.stderr)
    dup = duplicate_runs(lab.view(_actor(args)), run) if run["body"]["label"] == "confirmatory" else []
    if dup:
        print(f"claimset: warning: {run['id']} repeats {', '.join(dup)} (same command, code version, seeds and "
              f"parameters under another plan); one plan can name several hypotheses, so one run can decide "
              f"them all", file=sys.stderr)
    cfg = _anchor.anchoring(lab)
    if cfg and cfg.get("every") == "event":
        _auto_anchor(lab, out["results"][-1] if out["results"] else run)
    code = out["exit_code"]
    return 1 if code is None else (code if code >= 0 else 1)


def _view(args):
    """The state the current actor sees: its open agent log on top of the main log, or the main log."""
    return _lab(args).view(_actor(args, required=False))


def cmd_show(args):
    st = _view(args)
    e = st.get(args.id)
    if e is None:
        raise LabError("L12", f"no event '{args.id}'")
    latest = st.latest(args.id)
    chain = []
    cur = st.root(args.id)
    chain.append(cur)
    while cur in st.superseded_by:
        cur = st.superseded_by[cur]
        chain.append(cur)
    out = {"event": latest, "correction_chain": chain if len(chain) > 1 else []}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_list(args):
    events = _view(args).events
    if args.type:
        events = [e for e in events if e["type"] == args.type]
    if args.last:
        events = events[-args.last:]
    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
    else:
        for e in events:
            print(_summary.list_line(e))
    return 0


def cmd_anchor(args):
    lab = _lab(args)
    cfg = _anchor.load_config(lab)
    if args.git or args.remote or args.branch or args.every:
        current = cfg.get("anchor") or {}
        cfg["anchor"] = {"service": "git_remote",
                         "remote": args.remote or current.get("remote", "origin"),
                         "branch": args.branch or current.get("branch", _anchor.DEFAULT_BRANCH),
                         "every": args.every or current.get("every", "plan")}
        _anchor.save_config(lab, cfg)
    entry = _anchor.anchor(lab)
    if args.json:
        print(json.dumps(entry, indent=2))
    else:
        r = entry["receipt"]
        print(f"anchored seq {entry['seq']} at {r['remote']}/{r['branch']} {r['commit'][:12]}")
    return 0


def cmd_guard(args):
    if args.print_config:
        print(json.dumps(_guard.claude_code_config(_guard.guard_command()), indent=2))
        return 0
    return _guard.main_hook(sys.stdin, sys.stderr)


def cmd_protect(args):
    out = _protect.protect(_lab(args), undo=args.undo)
    for line in out["messages"]:
        print(line)
    return 0 if out["ok"] else 1


def cmd_start(args):
    lab = _lab(args)
    by = _actor(args)
    path = lab.start_agent(by)
    print(f"{by} now writes to {path} until `claimset lab merge {by}`")
    return 0


def cmd_merge(args):
    lab = _lab(args)
    event = lab.merge(_actor(args), args.actors or None)
    if args.json:
        print(json.dumps(event, ensure_ascii=False, indent=2))
    else:
        b = event["body"]
        print(f"{event['id']}: merged {b['events_merged']} events from {', '.join(b['agents'])}")
    _auto_anchor(lab, event)
    return 0


def cmd_export(args):
    from ..validate import validate

    lab = _lab(args)
    paper = _export.select_paper(lab, args.paper)
    if args.output:
        out_path = Path(args.output)
    else:
        st = lab.state()
        name = (paper or {}).get("name") or st.study
        out_path = (_layout.paper_path(st.study, name, lab.root) if st.study and name
                    else Path("claimset.draft.json"))
    if out_path.exists() and not args.force:
        raise LabError("export", f"{out_path} exists; choose another --output or add --force")
    out = _export.export(lab, paper)
    try:
        out_path.write_text(json.dumps(out["draft"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise LabError("export", f"could not write {out_path}: {exc.strerror or exc}") from None
    rep = validate(out["draft"])
    if args.json:
        print(json.dumps({"output": str(out_path), "mapping": out["mapping"], "completion": out["completion"],
                          "validation": {"errors": rep.errors, "warnings": rep.warnings}},
                         ensure_ascii=False, indent=2))
        return 0
    draft = out["draft"]
    print(f"Wrote {out_path}: {len(draft['claims'])} claims, {len(draft['evidence'])} evidence, "
          f"{len(draft['contributors'])} contributors, {len(draft['artefacts'])} artefacts.")
    for k, v in out["mapping"].items():
        if k.startswith("C"):
            c = next(x for x in draft["claims"] if x["id"] == k)
            label = (f"{c['hypothesis']['outcome']}, {c.get('planning')}" if "hypothesis" in c else "assumption")
            print(f"  {k} <- {v} ({label})")
    print("To complete before submission:")
    for item in out["completion"]:
        print(f"  - {item['where']}: {item['what']}")
    print(f"Validator: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s) in the draft; "
          f"`claimset validate {out_path}` lists them.")
    return 0


def cmd_verify(args):
    rep = verify(_lab(args), check_files=not args.no_files, remote=args.remote)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        state = "OK" if rep["ok"] else "FAILED"
        print(f"{state}: {rep['events']} events, {len(rep['errors'])} error(s), {rep['withheld']} withheld")
        for e in rep["errors"]:
            where = f"{e['log']} " if e.get("log") else ""
            print(f"  ERROR [{e['rule']}] {where}seq {e['seq']} {e['id'] or ''}: {e['message']}")
        for a, x in (rep.get("agents") or {}).items():
            state = f"merged by {x['merged_by']}" if x["merged_by"] else "open"
            print(f"  agent log {a}: {x['events']} events, {state}")
        for w in rep.get("warnings") or []:
            where = f"{w['log']} " if w.get("log") else ""
            print(f"  WARNING [{w['rule']}] {where}seq {w['seq']}: {w['message']}")
        for n in rep.get("notes") or []:
            print(f"  NOTE {n}")
        if rep["unchecked_types"]:
            print("  Not yet checked by this version: " + ", ".join(rep["unchecked_types"]))
    return 0 if rep["ok"] else 1


def cmd_summary(args):
    lab = _lab(args)
    main = lab.read()
    st = lab.view(_actor(args, required=False), main)
    s = _summary.build(st.events)
    if st.owner:
        s["view"] = {"actor": st.owner, "own_events": len(st.agent_events), "base": st.base,
                     "newer_main_events": len(main) - st.base}
    merged = lab.view(main=main).merged
    s["agent_logs"] = {a: {"events": len(lab.read_agent(a)), "merged_by": merged.get(a)} for a in lab.agent_logs()}
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(_summary.render(s))
    return 0


# ---------------------------------------------------------------- parser

def add_parser(subparsers):
    lab = subparsers.add_parser("lab", help="keep a lab record of research as it happens")
    lab_sub = lab.add_subparsers(dest="lab_command", metavar="lab-command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lab", help="lab directory (default: $CLAIMSET_LAB, else the study's lab)")
    common.add_argument("--study", help="the study whose lab to use (claimset/STUDY/lab; default: "
                                        "$CLAIMSET_STUDY, or the only study in the repository)")
    common.add_argument("--json", action="store_true", help="print JSON")

    writer = argparse.ArgumentParser(add_help=False, parents=[common])
    writer.add_argument("--by", help="actor ID of the writer (default: $CLAIMSET_ACTOR)")
    writer.add_argument("--supersedes", metavar="ID", help="write this event as a correction of ID")

    viewer = argparse.ArgumentParser(add_help=False, parents=[common])
    viewer.add_argument("--by", help="read as this actor: its open agent log included (default: $CLAIMSET_ACTOR)")

    def cmd(name, func, help_, parents=(writer,)):
        p = lab_sub.add_parser(name, help=help_, parents=list(parents))
        p.set_defaults(func=func)
        return p

    p = lab_sub.add_parser("init", help="create a lab record", parents=[common])
    p.add_argument("--title", required=True, help="working title of the research")
    p.add_argument("--repository", help="URI of the project repository")
    p.set_defaults(func=cmd_init)

    p = cmd("paper", cmd_paper, "record a paper drawn from the record and the questions it reports")
    p.add_argument("name", help="the paper's name (lower-case letters, digits, hyphens): "
                                "claimset/<study>/<name>.claimset.json")
    p.add_argument("--questions", nargs="+", required=True, metavar="ID", help="the questions it reports")
    p.add_argument("--title", help="working title of the paper")

    p = cmd("move", cmd_move, "move the lab (and files it names) to a new place, recording the move")
    p.add_argument("--to", help="new lab directory (default claimset/<study>/lab)")
    p.add_argument("--also", nargs="+", default=[], metavar="OLD=NEW", help="other files or folders to move")

    p = cmd("actor", cmd_actor, "register an agent, person or organisation")
    p.add_argument("--kind", required=True, choices=["agent", "human", "organisation"])
    p.add_argument("--name", required=True)
    p.add_argument("--model", action="append", default=[],
                   help="'provider|name|version[|identifier]'; repeat for several models")
    p.add_argument("--orcid")
    p.add_argument("--record-source", choices=["harness", "user_reported", "self_reported"])

    p = cmd("question", cmd_question, "record a research question")
    p.add_argument("text")

    p = cmd("term", cmd_term, "record a term: a method, data set, population, design, variable, concept or "
                              "theory the study names (the claimset's definitions are exported from these)")
    p.add_argument("term", help="the term as the study writes it, e.g. \"MASE\"")
    p.add_argument("--kind", required=True,
                   choices=["method", "intervention", "population", "data", "setting", "design", "variable",
                            "concept", "theory", "other"])
    p.add_argument("--definition", required=True,
                   help="what the term means here; for the study's own method, every defining equation")
    p.add_argument("--source", help="where a borrowed term is defined: a DOI, arXiv ID or citation")

    p = cmd("hypothesis", cmd_hypothesis, "record a hypothesis")
    p.add_argument("statement", help="mark the terms it names: [[TM3:MASE]]")
    p.add_argument("--question", required=True, help="question ID")
    p.add_argument("--from-exploration", action="store_true",
                   help="the hypothesis was suggested by results (give --prompted-by)")
    p.add_argument("--prompted-by", nargs="+", default=[], metavar="ID")
    p.add_argument("--subject", metavar="TM|TEXT", help="about: what the hypothesis is about (a term ID or text)")
    p.add_argument("--relation", metavar="TEXT", help="about: what is asserted of the subject, e.g. \"has lower "
                                                       "error than\"")
    p.add_argument("--object", nargs="+", metavar="TM|TEXT",
                   help="about: the variable, outcome, property or theme (several variable terms for one atom)")
    p.add_argument("--comparator", metavar="TM|TEXT", help="about: what the subject is compared with")
    p.add_argument("--condition", action="append", default=[], metavar="TM|TEXT",
                   help="about: a data set, population, setting or other condition; repeat for several")

    p = cmd("assume", cmd_assume, "record an assumption")
    p.add_argument("statement")
    p.add_argument("--category", required=True,
                   choices=["theoretical", "statistical", "methodological", "data", "axiom",
                            "theorem_condition", "paradigmatic"])
    p.add_argument("--check", required=True, choices=["tested", "sensitivity_analysed", "unchecked", "untestable"])
    p.add_argument("--check-evidence", metavar="ID")

    p = cmd("note", cmd_note, "record an observation")
    p.add_argument("text")
    p.add_argument("--refers-to", nargs="+", default=[], metavar="ID")

    p = cmd("decide", cmd_decide, "record a decision and its reason")
    p.add_argument("text", help="the decision and why it was taken")
    p.add_argument("--prompted-by", nargs="+", default=[], metavar="ID")
    p.add_argument("--affects", nargs="+", default=[], metavar="ID")

    p = cmd("status", cmd_status, "change a hypothesis's status")
    p.add_argument("target", help="hypothesis ID")
    p.add_argument("to", choices=["testing", "supported", "not_supported", "inconclusive", "revised", "abandoned"])
    p.add_argument("--why", required=True, help="rationale")
    p.add_argument("--evidence", nargs="+", default=[], metavar="ID")
    p.add_argument("--revised-to", metavar="ID")
    p.add_argument("--plan", metavar="ID", help="the decided plan whose verdict this status reports")
    p.add_argument("--rule", choices=["supported", "not_supported", "neither"],
                   help="for plans with text rules: which rule the evidence met")

    p = cmd("check", cmd_check, "record an independent check of an event")
    p.add_argument("target")
    p.add_argument("--outcome", required=True, choices=["confirmed", "disputed", "inconclusive"])
    p.add_argument("--extent", required=True, choices=["full", "partial"])
    p.add_argument("--notes")
    p.add_argument("--resolution")

    p = cmd("artefact", cmd_artefact, "register a file or resource (hashed if it is a local file)")
    p.add_argument("path_or_uri")
    p.add_argument("--kind", required=True,
                   choices=["code", "data", "protocol", "proof", "model", "software", "figure", "table",
                            "prompt", "trace", "manuscript", "other"])
    p.add_argument("--description", required=True)
    p.add_argument("--licence")
    p.add_argument("--hash")

    p = cmd("plan", cmd_plan, "record a plan before running it")
    p.add_argument("--kind", required=True, choices=["confirmatory", "exploratory"])
    p.add_argument("--hypotheses", nargs="+", default=[], metavar="ID",
                   help="hypotheses the plan tests (required for confirmatory)")
    p.add_argument("--design", help="what will be run, on what, with which settings (required, except with "
                                     "--comment: the test's procedure)")
    p.add_argument("--analysis", required=True, help="what will be computed from the outputs")
    p.add_argument("--requires-approval", dest="requires_approval", action="store_true",
                   help="no confirmatory run until the supervisor's approval of this plan is recorded")
    for role, what in (("studied", "what the test studies or compares: methods, interventions, a phenomenon"),
                       ("cases", "what or whom it is tested on: data sets, populations, participants"),
                       ("procedure", "how: evaluation scheme, trial design, sampling, analytic approach"),
                       ("lens", "what is measured or how it is read: measures, outcomes, instruments, a coding "
                                "frame"),
                       ("context", "where and when, when it bounds the test")):
        p.add_argument(f"--{role}", nargs="+", default=[], metavar="TM", help=f"setup: {what} (term IDs)")
    p.add_argument("--comment", metavar="ID",
                   help="run a review comment's resolution test: its global ID (<claimset>@v<n>/RV<k>.c<m>); the "
                        "decision rule is the test (supported: its withdrawn rule; not supported: its upheld rule)")
    p.add_argument("--review", action="append", default=[], metavar="FILE",
                   help="the review holding the comment, and any re-review that replaced its test")
    p.add_argument("--addresses", nargs="+", metavar="ID", help="review comments whose resolution test the plan runs")
    p.add_argument("--new-data", dest="new_data", metavar="TEXT",
                   help="what this test uses that the runs which suggested its hypotheses did not")
    p.add_argument("--exclusion", action="append", default=[], metavar="NAME=RULE",
                   help="a rule that leaves units out of the analysis, e.g. edge=\"models within 1e-8 of the "
                        "boundary\"; the runs should report how many it excluded; repeat for several")
    p.add_argument("--framework", choices=list(_decision.FRAMEWORKS), help="how the outcome will be decided")
    p.add_argument("--parameter", action="append", default=[], metavar="NAME=VALUE",
                   help="a parameter fixed in advance, e.g. alpha=0.05, sides=greater; repeat for several")
    p.add_argument("--quantity", action="append", default=[], metavar="NAME=QUANTITY",
                   help="a result the rules use: name=\"quantity text\", or name='{\"quantity\": …, "
                        "\"conditions\": {…}, \"field\": \"interval.lower\"}'; repeat for several")
    p.add_argument("--supported", help="rule for supported: an expression over quantities and parameters, "
                                       "or text for formal and qualitative plans")
    p.add_argument("--not-supported", dest="not_supported", help="rule for not_supported (refuted)")
    p.add_argument("--runs", type=int, help="how many successful confirmatory runs decide the plan (default 1)")
    p.add_argument("--decision", metavar="FILE", help="the whole decision rule as a JSON file")
    p.add_argument("--holdout", action="append", default=[], metavar="FILE",
                   help="data kept for this test only (hashed); runs that use it elsewhere are flagged")
    p.add_argument("--criterion", help="records begun under lab record v0.4 or earlier only")
    p.add_argument("--data", action="append", default=[], metavar="FILE",
                   help="data file the plan uses (hashed); repeat for several")

    p = cmd("verdict", cmd_verdict, "the verdict a decided plan's rule gives", parents=(viewer,))
    p.add_argument("plan", help="plan ID")

    p = cmd("approve", cmd_approve, "record an approval of a plan or other event: by the human, or by an agent on "
                                    "the human's behalf with their words")
    p.add_argument("target")
    p.add_argument("--comment")
    p.add_argument("--on-behalf-of", dest="on_behalf_of", metavar="AC",
                   help="the human actor whose approval an agent records (the agent writes with --by)")
    p.add_argument("--words", help="the human's reply, verbatim (required with --on-behalf-of)")
    p.add_argument("--given-at", dest="given_at", metavar="TIME", help="when the human gave it (ISO 8601, UTC)")
    p.add_argument("--channel", help="where the human gave it: conversation, email, meeting notes, …")

    p = cmd("run", cmd_run, "run a command and record its run and results",
            parents=(common,))
    p.add_argument("--by", help="actor ID of the writer (default: $CLAIMSET_ACTOR)")
    p.add_argument("--plan", metavar="ID", help="plan this run carries out")
    p.add_argument("--exploratory", action="store_true", help="label the run exploratory even under a plan")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--seed", action="append", type=int, default=[])
    p.add_argument("--data", action="append", default=[], metavar="FILE", help="input file (hashed)")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- command and arguments")

    p = cmd("deviate", cmd_deviate, "record that a run departed from its plan")
    p.add_argument("--plan", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--text", required=True, help="what differed")
    p.add_argument("--reason", required=True, help="why")

    p = cmd("start", cmd_start, "start an agent log: this actor's events go there until it is merged")

    p = cmd("merge", cmd_merge, "merge finished agent logs into the main log")
    p.add_argument("actors", nargs="*", metavar="ACTOR", help="agent logs to merge (default: all open ones)")


    p = cmd("show", cmd_show, "show one event, with corrections resolved", parents=(viewer,))
    p.add_argument("id")

    p = cmd("list", cmd_list, "list events, one per line", parents=(viewer,))
    p.add_argument("--type")
    p.add_argument("--last", type=int)

    p = cmd("verify", cmd_verify, "check the hash chain, the rules and the run files", parents=(common,))
    p.add_argument("--no-files", action="store_true", help="skip checking run directories against their hashes")
    p.add_argument("--remote", action="store_true", help="also check the log against the anchor branch on the remote")

    p = cmd("export", cmd_export, "draft a claimset submission from the record", parents=(common,))
    p.add_argument("--paper", help="the paper to export (a `claimset lab paper` name): its questions only")
    p.add_argument("-o", "--output", help="draft file (default claimset/<study>/<paper>.claimset.json, or "
                                          "claimset.draft.json for a lab without a study)")
    p.add_argument("--force", action="store_true", help="overwrite the draft file")

    p = cmd("anchor", cmd_anchor, "record the head of the log outside the agent's reach", parents=(common,))
    p.add_argument("--git", action="store_true", help="use git anchoring (records the configuration)")
    p.add_argument("--remote", help="git remote (default origin)")
    p.add_argument("--branch", help=f"anchor branch (default {_anchor.DEFAULT_BRANCH})")
    p.add_argument("--every", choices=["plan", "event"], help="anchor after every plan (default) or every event")

    p = lab_sub.add_parser("guard", help="hook for agent harnesses: refuse tool calls that would modify the lab")
    p.add_argument("--print-config", action="store_true", help="print the Claude Code hook configuration")
    p.set_defaults(func=cmd_guard)

    p = cmd("protect", cmd_protect, "make the log files append-only at the operating-system level",
            parents=(common,))
    p.add_argument("--undo", action="store_true", help="remove the append-only attribute")
    cmd("summary", cmd_summary, "current state of the research", parents=(viewer,))

    def lab_help(args):
        lab.print_help()
        return 2

    lab.set_defaults(func=lab_help)
    return lab


def run(func, args):
    """Run a handler, turning LabError into a message and exit code 1. A command that writes is refused unless the
    lab is on its study branch (or a lab outside the study layout); afterwards, what changed is committed to the
    study branch and pushed (§3, §7.1)."""
    try:
        command = getattr(args, "lab_command", None)
        lab, before = None, 0
        if command and command not in READERS and command not in ("init", "move"):
            lab = _lab(args)
            if lab.exists():
                _record.check_writable(lab)
                _record.migrate(lab)          # a receipt an earlier tool left uncommitted (lab record v0.15)
                before = len(lab.read())
        code = func(args)
        if lab is not None and lab.exists() and _record.config(lab):
            entry = _record.save(lab, before)
            if entry:
                print(f"claimset: pushed {entry['receipt']['branch']} at seq {entry['seq']} "
                      f"({entry['receipt']['commit'][:12]})", file=sys.stderr)
        return code
    except LabError as exc:
        print(f"ERROR [{exc.rule}] {exc.message}", file=sys.stderr)
        return 1
