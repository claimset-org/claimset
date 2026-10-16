"""`claimset lab ...` commands (lab record spec §10)."""

import argparse
import json
import os
import sys
from pathlib import Path

from .core import Lab, LabError, file_hash, verify
from . import summary as _summary
from .run import run_command
from . import anchor as _anchor
from . import guard as _guard
from . import protect as _protect


def _lab(args):
    return Lab(args.lab or os.environ.get("CLAIMSET_LAB") or "lab")


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
    """Anchor after a plan (or after every event, if configured); warn instead of failing."""
    cfg = _anchor.anchoring(lab)
    if not cfg:
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
    lab = Lab.create(args.lab or os.environ.get("CLAIMSET_LAB") or "lab", args.title, args.repository)
    print(f"INIT {lab.log_path}")
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


def cmd_hypothesis(args):
    return _write(args, "hypothesis", {
        "statement": args.statement, "question": args.question,
        "origin": "from_exploration" if args.from_exploration else "a_priori",
        "prompted_by": args.prompted_by})


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
                                   "evidence": args.evidence, "revised_to": args.revised_to})


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


def cmd_plan(args):
    data = [{"description": d, "hash": file_hash(d)} if Path(d).is_file() else {"description": d}
            for d in args.data]
    return _write(args, "plan", {"kind": args.kind, "hypotheses": args.hypotheses, "design": args.design,
                                 "analysis": args.analysis, "criterion": args.criterion, "data": data})


def cmd_approve(args):
    return _write(args, "approval", {"target": args.target, "comment": args.comment})


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
        print(json.dumps(_guard.claude_code_config(), indent=2))
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
    common.add_argument("--lab", help="lab directory (default: $CLAIMSET_LAB or ./lab)")
    common.add_argument("--json", action="store_true", help="print JSON")

    writer = argparse.ArgumentParser(add_help=False, parents=[common])
    writer.add_argument("--by", help="actor ID of the writer (default: $CLAIMSET_ACTOR)")
    writer.add_argument("--supersedes", metavar="ID", help="write this event as a correction of ID")

    def cmd(name, func, help_, parents=(writer,)):
        p = lab_sub.add_parser(name, help=help_, parents=list(parents))
        p.set_defaults(func=func)
        return p

    p = lab_sub.add_parser("init", help="create a lab record", parents=[common])
    p.add_argument("--title", required=True, help="working title of the research")
    p.add_argument("--repository", help="URI of the project repository")
    p.set_defaults(func=cmd_init)

    p = cmd("actor", cmd_actor, "register an agent, person or organisation")
    p.add_argument("--kind", required=True, choices=["agent", "human", "organisation"])
    p.add_argument("--name", required=True)
    p.add_argument("--model", action="append", default=[],
                   help="'provider|name|version[|identifier]'; repeat for several models")
    p.add_argument("--orcid")
    p.add_argument("--record-source", choices=["harness", "user_reported", "self_reported"])

    p = cmd("question", cmd_question, "record a research question")
    p.add_argument("text")

    p = cmd("hypothesis", cmd_hypothesis, "record a hypothesis")
    p.add_argument("statement")
    p.add_argument("--question", required=True, help="question ID")
    p.add_argument("--from-exploration", action="store_true",
                   help="the hypothesis was suggested by results (give --prompted-by)")
    p.add_argument("--prompted-by", nargs="+", default=[], metavar="ID")

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
    p.add_argument("--design", required=True, help="what will be run, on what, with which settings")
    p.add_argument("--analysis", required=True, help="what will be computed from the outputs")
    p.add_argument("--criterion", help="how the outcome will be judged (required for confirmatory)")
    p.add_argument("--data", action="append", default=[], metavar="FILE",
                   help="data file the plan uses (hashed); repeat for several")

    p = cmd("approve", cmd_approve, "approve a plan or other event (human actors only)")
    p.add_argument("target")
    p.add_argument("--comment")

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

    viewer = argparse.ArgumentParser(add_help=False, parents=[common])
    viewer.add_argument("--by", help="read as this actor: its open agent log included (default: $CLAIMSET_ACTOR)")

    p = cmd("show", cmd_show, "show one event, with corrections resolved", parents=(viewer,))
    p.add_argument("id")

    p = cmd("list", cmd_list, "list events, one per line", parents=(viewer,))
    p.add_argument("--type")
    p.add_argument("--last", type=int)

    p = cmd("verify", cmd_verify, "check the hash chain, the rules and the run files", parents=(common,))
    p.add_argument("--no-files", action="store_true", help="skip checking run directories against their hashes")
    p.add_argument("--remote", action="store_true", help="also check the log against the anchor branch on the remote")

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
    """Run a handler, turning LabError into a message and exit code 1."""
    try:
        return func(args)
    except LabError as exc:
        print(f"ERROR [{exc.rule}] {exc.message}", file=sys.stderr)
        return 1
