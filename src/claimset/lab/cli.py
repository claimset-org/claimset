"""`claimset lab ...` commands (lab record spec §10)."""

import argparse
import json
import os
import sys
from pathlib import Path

from .core import Lab, LabError, file_hash, verify
from . import summary as _summary


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


def _write(args, etype, body, by=None):
    lab = _lab(args)
    by = _actor(args) if by is None else by
    event = lab.append(etype, _clean(body), by=by, supersedes=getattr(args, "supersedes", None))
    _emit(args, event)
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


def cmd_show(args):
    st = _lab(args).state()
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
    events = _lab(args).read()
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


def cmd_verify(args):
    rep = verify(_lab(args))
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        state = "OK" if rep["ok"] else "FAILED"
        print(f"{state}: {rep['events']} events, {len(rep['errors'])} error(s), {rep['withheld']} withheld")
        for e in rep["errors"]:
            print(f"  ERROR [{e['rule']}] seq {e['seq']} {e['id'] or ''}: {e['message']}")
        if rep["unchecked_types"]:
            print("  Not yet checked by this version: " + ", ".join(rep["unchecked_types"]))
    return 0 if rep["ok"] else 1


def cmd_summary(args):
    s = _summary.build(_lab(args).read())
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

    p = cmd("show", cmd_show, "show one event, with corrections resolved", parents=(common,))
    p.add_argument("id")

    p = cmd("list", cmd_list, "list events, one per line", parents=(common,))
    p.add_argument("--type")
    p.add_argument("--last", type=int)

    cmd("verify", cmd_verify, "check the hash chain and the rules", parents=(common,))
    cmd("summary", cmd_summary, "current state of the research", parents=(common,))

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
