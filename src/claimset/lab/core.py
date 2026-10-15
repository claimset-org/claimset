"""The lab record: an append-only, hash-chained event log (lab record spec v0.3).

This module reads, writes and verifies `lab/lab.jsonl`. Everything that writes
the log goes through `Lab.append`, which takes the lock, checks the new event
against the log so far and refuses it if it breaks a rule.
"""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import LAB_SPEC_VERSION

HEADER_FIELDS = ("seq", "id", "type", "at", "by", "prev", "content_hash", "supersedes", "origin")

PREFIX = {
    "actor": "AC", "question": "Q", "hypothesis": "H", "status": "ST", "assumption": "AS",
    "plan": "PL", "approval": "AP", "run": "RN", "result": "RS", "decision": "DC",
    "deviation": "DV", "check": "CK", "artefact": "AR", "note": "NT", "merge": "MG",
}
INIT_ID = "INIT"

#: Event types whose bodies this version of the tool can write and fully check.
IMPLEMENTED = {"init", "actor", "question", "hypothesis", "status", "assumption",
               "decision", "check", "artefact", "note", "plan", "approval", "run", "result",
               "deviation"}

ENUM = {
    "actor_kind": {"agent", "human", "organisation"},
    "record_source": {"harness", "user_reported", "self_reported"},
    "origin": {"a_priori", "from_exploration"},
    "status_to": {"testing", "supported", "not_supported", "inconclusive", "revised", "abandoned"},
    "assumption_category": {"theoretical", "statistical", "methodological", "data", "axiom",
                            "theorem_condition", "paradigmatic"},
    "assumption_check": {"tested", "sensitivity_analysed", "unchecked", "untestable"},
    "check_outcome": {"confirmed", "disputed", "inconclusive"},
    "check_extent": {"full", "partial"},
    "artefact_kind": {"code", "data", "protocol", "proof", "model", "software", "figure", "table",
                      "prompt", "trace", "manuscript", "other"},
    "withheld_reason": {"personal_data", "commercial", "legal", "ethics", "security", "other"},
    "plan_kind": {"confirmatory", "exploratory"},
    "run_label": {"confirmatory", "exploratory"},
    "exit_status": {"success", "failed", "crashed", "cancelled"},
}

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
ID_RE = re.compile(r"^(?:AC\d+\.)?([A-Z]+)(\d+)$")
LOCK_STALE_SECONDS = 60
LOCK_TIMEOUT_SECONDS = 10


class LabError(Exception):
    """A refused write or a failed check, tagged with the rule it concerns."""

    def __init__(self, rule, message):
        super().__init__(f"[{rule}] {message}")
        self.rule = rule
        self.message = message


# ---------------------------------------------------------------- hashing

def canonical(value):
    """Canonical JSON bytes (spec §4.1)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def content_hash(body):
    return sha256(canonical(body))


def header_of(event):
    return {k: event.get(k) for k in HEADER_FIELDS}


def header_hash(event):
    return sha256(canonical(header_of(event)))


GENESIS_PREV = sha256(b"")


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_withheld(event):
    return isinstance(event.get("body"), dict) and event["body"].get("withheld") is True


# ---------------------------------------------------------------- state

class State:
    """Indexes of the log so far, used to check each new event against it."""

    def __init__(self):
        self.events = []
        self.by_id = {}
        self.max_num = {}
        self.superseded_by = {}
        self.root_of = {}
        self.actors = set()

    # -- lookups
    def get(self, eid):
        return self.by_id.get(eid)

    def root(self, eid):
        return self.root_of.get(eid, eid)

    def latest(self, eid):
        """The latest event of the correction chain containing `eid`."""
        e = self.by_id.get(eid)
        while e is not None and e["id"] in self.superseded_by:
            e = self.by_id[self.superseded_by[e["id"]]]
        return e

    def type_of(self, eid):
        e = self.by_id.get(eid)
        return e["type"] if e else None

    def next_id(self, etype):
        if etype == "init":
            return INIT_ID
        prefix = PREFIX[etype]
        return f"{prefix}{self.max_num.get(prefix, 0) + 1}"

    def head(self):
        return self.events[-1] if self.events else None

    # -- update
    def apply(self, event):
        self.events.append(event)
        eid = event["id"]
        self.by_id[eid] = event
        m = ID_RE.match(eid)
        if m:
            p, n = m.group(1), int(m.group(2))
            self.max_num[p] = max(self.max_num.get(p, 0), n)
        sup = event.get("supersedes")
        if sup:
            self.superseded_by[sup] = eid
            self.root_of[eid] = self.root(sup)
        if event["type"] == "actor":
            self.actors.add(eid)

    # -- checks for a new event (rules L6-L9, L11, L12 and body schemas)
    def check(self, etype, eid, by, body, supersedes):
        if etype not in PREFIX and etype != "init":
            raise LabError("L12", f"unknown event type '{etype}'")
        if etype == "init":
            if self.events:
                raise LabError("L12", "init must be the first event")
            if by is not None:
                raise LabError("L8", "init has by: null")
        else:
            if not self.events:
                raise LabError("L12", "the first event must be init")
            if not (by in self.actors or (etype == "actor" and by == eid)):
                raise LabError("L8", f"'{by}' is not a registered actor (register with `claimset lab actor`)")
        if supersedes is not None:
            old = self.get(supersedes)
            if old is None:
                raise LabError("L11", f"supersedes '{supersedes}', which does not exist")
            if old["type"] != etype:
                raise LabError("L11", f"a correction must have the same type as {supersedes} ({old['type']})")
            if supersedes in self.superseded_by:
                raise LabError("L11", f"{supersedes} is already superseded by {self.superseded_by[supersedes]}; "
                                      f"correct that one instead")
        if not isinstance(body, dict):
            raise LabError("schema", "body must be an object")
        checker = BODY_CHECKS.get(etype)
        if checker:
            checker(self, body, by, eid)


# ---------------------------------------------------------------- body checks

def _req(body, field, etype):
    v = body.get(field)
    if v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v):
        raise LabError("schema", f"{etype}: '{field}' is required")
    return v


def _enum(body, field, enum_name, etype, required=True):
    v = body.get(field)
    if v is None:
        if required:
            raise LabError("schema", f"{etype}: '{field}' is required")
        return None
    if v not in ENUM[enum_name]:
        raise LabError("schema", f"{etype}: '{field}' must be one of {sorted(ENUM[enum_name])}, not '{v}'")
    return v


def _refs(state, ids, etype, field, types=None):
    if ids is None:
        return []
    if not isinstance(ids, list):
        raise LabError("schema", f"{etype}: '{field}' must be a list of event IDs")
    for i in ids:
        e = state.get(i)
        if e is None:
            raise LabError("L12", f"{etype}: '{field}' names '{i}', which is not an earlier event")
        if types and e["type"] not in types:
            raise LabError("schema", f"{etype}: '{field}' must name {' or '.join(sorted(types))} events; "
                                     f"'{i}' is a {e['type']}")
    return ids


def _ref(state, eid, etype, field, types=None):
    return _refs(state, [eid], etype, field, types)[0]


def check_init(state, body, by, eid):
    _req(body, "lab_spec_version", "init")
    _req(body, "working_title", "init")


def check_actor(state, body, by, eid):
    kind = _enum(body, "kind", "actor_kind", "actor")
    _req(body, "name", "actor")
    _enum(body, "record_source", "record_source", "actor", required=False)
    if kind == "agent":
        models = body.get("models")
        if not isinstance(models, list) or not models:
            raise LabError("schema", "actor: an agent needs at least one model {provider, name, version}")
        for m in models:
            if not isinstance(m, dict) or not all(m.get(k) for k in ("provider", "name", "version")):
                raise LabError("schema", "actor: each model needs provider, name and version")
    if body.get("orcid") and not ORCID_RE.match(str(body["orcid"])):
        raise LabError("schema", "actor: orcid must look like 0000-0002-1825-0097")


def check_question(state, body, by, eid):
    _req(body, "text", "question")


def check_hypothesis(state, body, by, eid):
    _req(body, "statement", "hypothesis")
    _ref(state, _req(body, "question", "hypothesis"), "hypothesis", "question", {"question"})
    origin = _enum(body, "origin", "origin", "hypothesis")
    prompted = body.get("prompted_by") or []
    if origin == "from_exploration" and not prompted:
        raise LabError("schema", "hypothesis: from_exploration requires prompted_by (the events that suggested it)")
    _refs(state, prompted, "hypothesis", "prompted_by")


def check_status(state, body, by, eid):
    target = _ref(state, _req(body, "target", "status"), "status", "target", {"hypothesis"})
    to = _enum(body, "to", "status_to", "status")
    rationale = body.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise LabError("L7", "status: a rationale is required for every status change")
    evidence = body.get("evidence") or []
    _refs(state, evidence, "status", "evidence")
    if to in ("supported", "not_supported", "inconclusive") and not evidence:
        raise LabError("L7", f"status: '{to}' requires evidence (run, result or other event IDs)")
    if to == "revised":
        _ref(state, _req(body, "revised_to", "status"), "status", "revised_to", {"hypothesis"})
    if to == "supported":
        hyp = state.latest(target)
        prompted = set(hyp["body"].get("prompted_by") or []) if not is_withheld(hyp) else set()
        prompted_runs = {p for p in prompted if state.type_of(p) == "run"}
        for e in evidence:
            if e in prompted:
                raise LabError("L6", f"status: {e} suggested hypothesis {target} and cannot also confirm it; "
                                     f"confirm it with a new run")
            ev = state.get(e)
            if ev and ev["type"] == "result" and not is_withheld(ev) and ev["body"].get("run") in prompted_runs:
                raise LabError("L6", f"status: {e} comes from run {ev['body']['run']}, which suggested "
                                     f"hypothesis {target}; confirm it with a new run")


def check_assumption(state, body, by, eid):
    _req(body, "statement", "assumption")
    _enum(body, "category", "assumption_category", "assumption")
    chk = _enum(body, "check", "assumption_check", "assumption")
    if chk in ("tested", "sensitivity_analysed"):
        _ref(state, _req(body, "check_evidence", "assumption"), "assumption", "check_evidence")
    if body.get("category") == "axiom" and chk != "untestable":
        raise LabError("schema", "assumption: an axiom has check 'untestable'")


def check_decision(state, body, by, eid):
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise LabError("L7", "decision: the decision and its reason are required in 'text'")
    _refs(state, body.get("prompted_by"), "decision", "prompted_by")
    _refs(state, body.get("affects"), "decision", "affects")


def check_check(state, body, by, eid):
    target = _ref(state, _req(body, "target", "check"), "check", "target")
    _enum(body, "outcome", "check_outcome", "check")
    _enum(body, "extent", "check_extent", "check")
    if body.get("outcome") == "disputed" and not (body.get("notes") or "").strip():
        raise LabError("schema", "check: a disputed check needs notes saying what failed")
    authors = {state.get(x)["by"] for x in _chain(state, target)}
    if by in authors:
        raise LabError("L9", f"check: {by} wrote {target} and cannot check it; use an independent actor")


def _chain(state, eid):
    """All IDs in the correction chain containing eid."""
    root = state.root(eid)
    out, cur = [root], root
    while cur in state.superseded_by:
        cur = state.superseded_by[cur]
        out.append(cur)
    return out


def check_artefact(state, body, by, eid):
    _enum(body, "kind", "artefact_kind", "artefact")
    _req(body, "path_or_uri", "artefact")
    if body.get("hash") and not HASH_RE.match(str(body["hash"])):
        raise LabError("schema", "artefact: hash must be sha256: followed by 64 hex digits")


def check_note(state, body, by, eid):
    _req(body, "text", "note")
    _refs(state, body.get("refers_to"), "note", "refers_to")


def check_plan(state, body, by, eid):
    kind = _enum(body, "kind", "plan_kind", "plan")
    _req(body, "design", "plan")
    _req(body, "analysis", "plan")
    hyps = body.get("hypotheses") or []
    _refs(state, hyps, "plan", "hypotheses", {"hypothesis"})
    if kind == "confirmatory":
        if not hyps:
            raise LabError("schema", "plan: a confirmatory plan names the hypotheses it tests")
        _req(body, "criterion", "plan")
    for d in body.get("data") or []:
        if not isinstance(d, dict) or not d.get("description"):
            raise LabError("schema", "plan: each data entry needs a description")
        if d.get("hash") and not HASH_RE.match(str(d["hash"])):
            raise LabError("schema", "plan: data hash must be sha256: followed by 64 hex digits")


def check_approval(state, body, by, eid):
    _ref(state, _req(body, "target", "approval"), "approval", "target")
    actor = state.latest(by)
    if actor is None or is_withheld(actor) or actor["body"].get("kind") != "human":
        raise LabError("schema", "approval: approvals are made by a human actor")


def check_run(state, body, by, eid):
    label = _enum(body, "label", "run_label", "run")
    _enum(body, "exit_status", "exit_status", "run")
    cmd = body.get("command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise LabError("schema", "run: command must be a non-empty list of strings")
    for f in ("outputs", "data"):
        for o in body.get(f) or []:
            if not isinstance(o, dict) or not o.get("path") or not HASH_RE.match(str(o.get("hash", ""))):
                raise LabError("schema", f"run: each {f} entry needs a path and a sha256 hash")
    plan_id = body.get("plan")
    if plan_id is not None:
        _ref(state, plan_id, "run", "plan", {"plan"})
    if label == "confirmatory":
        if plan_id is None:
            raise LabError("L5", "run: a confirmatory run names the confirmatory plan it carries out")
        plan = state.latest(plan_id)
        if is_withheld(plan) or plan["body"].get("kind") != "confirmatory":
            raise LabError("L5", f"run: {plan_id} is not a confirmatory plan, so the run is exploratory")


def check_result(state, body, by, eid):
    run_id = _ref(state, _req(body, "run", "result"), "result", "run", {"run"})
    _req(body, "quantity", "result")
    v = body.get("value")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise LabError("schema", "result: value must be a number")
    src = _req(body, "source", "result")
    run = state.get(run_id)
    if not is_withheld(run) and src not in {o.get("path") for o in run["body"].get("outputs") or []}:
        raise LabError("L4", f"result: source '{src}' is not an output of {run_id}")


def check_deviation(state, body, by, eid):
    _ref(state, _req(body, "plan", "deviation"), "deviation", "plan", {"plan"})
    _ref(state, _req(body, "run", "deviation"), "deviation", "run", {"run"})
    _req(body, "text", "deviation")
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise LabError("L7", "deviation: a reason is required")


BODY_CHECKS = {
    "init": check_init, "actor": check_actor, "question": check_question,
    "hypothesis": check_hypothesis, "status": check_status, "assumption": check_assumption,
    "decision": check_decision, "check": check_check, "artefact": check_artefact, "note": check_note,
    "plan": check_plan, "approval": check_approval, "run": check_run, "result": check_result,
    "deviation": check_deviation,
}


# ---------------------------------------------------------------- the lab

class Lab:
    """A lab directory holding `lab.jsonl`."""

    def __init__(self, root, clock=utc_now):
        self.root = Path(root)
        self.log_path = self.root / "lab.jsonl"
        self.lock_path = self.root / ".lock"
        self.clock = clock

    # -- creation and reading
    @classmethod
    def create(cls, root, title, repository=None, clock=utc_now):
        lab = cls(root, clock=clock)
        if lab.log_path.exists():
            raise LabError("init", f"{lab.log_path} already exists")
        lab.root.mkdir(parents=True, exist_ok=True)
        (lab.root / "runs").mkdir(exist_ok=True)
        (lab.root / "agents").mkdir(exist_ok=True)
        now = clock()
        lab.append("init", {"lab_spec_version": LAB_SPEC_VERSION, "working_title": title,
                            "repository": repository, "started_at": now}, by=None)
        return lab

    def exists(self):
        return self.log_path.exists()

    def read(self):
        """All events in the log, parsed. Raises LabError on unreadable lines."""
        if not self.log_path.exists():
            raise LabError("init", f"no lab record at {self.log_path}; run `claimset lab init` first")
        events = []
        with open(self.log_path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise LabError("L2", f"line {n} is not valid JSON: {exc}") from None
        return events

    def state(self):
        st = State()
        for e in self.read():
            st.apply(e)
        return st

    # -- writing
    def append(self, etype, body, by, supersedes=None):
        """Check and append one event; return it. Raises LabError if refused."""
        with self._lock():
            st = State()
            if self.log_path.exists():
                for e in self.read():
                    st.apply(e)
            eid = st.next_id(etype)
            st.check(etype, eid, by, body, supersedes)
            head = st.head()
            event = {
                "seq": len(st.events) + 1,
                "id": eid,
                "type": etype,
                "at": self.clock(),
                "by": by,
                "prev": header_hash(head) if head else GENESIS_PREV,
                "content_hash": content_hash(body),
                "supersedes": supersedes,
                "origin": None,
                "body": body,
            }
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return event

    def _lock(self):
        lab = self

        class _Lock:
            def __enter__(self_inner):
                deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
                lab.root.mkdir(parents=True, exist_ok=True)
                while True:
                    try:
                        fd = os.open(lab.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.write(fd, f"{os.getpid()} {time.time()}".encode())
                        os.close(fd)
                        return self_inner
                    except FileExistsError:
                        try:
                            if time.time() - lab.lock_path.stat().st_mtime > LOCK_STALE_SECONDS:
                                lab.lock_path.unlink()
                                continue
                        except FileNotFoundError:
                            continue
                        if time.monotonic() > deadline:
                            raise LabError("lock", f"{lab.lock_path} is held by another writer")
                        time.sleep(0.05)

            def __exit__(self_inner, *exc):
                try:
                    lab.lock_path.unlink()
                except FileNotFoundError:
                    pass
                return False

        return _Lock()


# ---------------------------------------------------------------- verification

def verify_events(events):
    """Check a sequence of parsed events against rules L2, L8, L9, L11, L12 and the body checks.

    Returns a report: {"ok", "events", "withheld", "unchecked_types", "errors": [{seq, id, rule, message}]}.
    """
    errors = []
    st = State()
    prev = GENESIS_PREV
    withheld = 0
    unchecked = set()
    for i, e in enumerate(events, start=1):
        if not isinstance(e, dict):
            errors.append({"seq": i, "id": None, "rule": "L2", "message": "event is not an object"})
            continue
        eid = e.get("id")

        def err(rule, msg):
            errors.append({"seq": e.get("seq"), "id": eid, "rule": rule, "message": msg})

        missing = [k for k in HEADER_FIELDS + ("body",) if k not in e]
        if missing:
            err("L2", f"missing fields {missing}")
            continue
        if e["seq"] != i:
            err("L2", f"seq is {e['seq']}, expected {i}: an event was removed, added or reordered")
        if e["prev"] != prev:
            err("L2", "prev does not match the previous event's header: the chain is broken here")
        prev = header_hash(e)
        if is_withheld(e):
            withheld += 1
            b = e["body"]
            if b.get("reason") not in ENUM["withheld_reason"]:
                err("L2", "withheld body needs a reason from the allowed list")
        elif content_hash(e["body"]) != e["content_hash"]:
            err("L2", "content_hash does not match the body: the body was changed after writing")
        etype = e["type"]
        expected_prefix = INIT_ID if etype == "init" else PREFIX.get(etype)
        m = ID_RE.match(str(eid))
        if etype == "init":
            if eid != INIT_ID:
                err("L12", "the init event's ID is INIT")
        elif expected_prefix is None:
            err("L12", f"unknown event type '{etype}'")
        elif not m or m.group(1) != expected_prefix:
            err("L12", f"ID '{eid}' does not have the prefix {expected_prefix} of type {etype}")
        if eid in st.by_id:
            err("L12", f"duplicate ID '{eid}'")
        if is_withheld(e) or etype not in IMPLEMENTED:
            if etype not in IMPLEMENTED and etype in PREFIX:
                unchecked.add(etype)
            st.apply(e)
            continue
        try:
            st.check(etype, eid, e["by"], e["body"], e["supersedes"])
        except LabError as exc:
            err(exc.rule, exc.message)
        st.apply(e)
    return {"ok": not errors, "events": len(events), "withheld": withheld,
            "unchecked_types": sorted(unchecked), "errors": errors}


def verify_run_files(lab, events):
    """Rule L13: each run directory holds exactly the files recorded in the run's outputs."""
    errors = []
    for e in events:
        if e.get("type") != "run" or is_withheld(e):
            continue
        rdir = lab.root / "runs" / e["id"]
        recorded = {o["path"]: o["hash"] for o in e["body"].get("outputs") or []}
        if not rdir.is_dir():
            if recorded:
                errors.append({"seq": e["seq"], "id": e["id"], "rule": "L13",
                               "message": f"run directory {rdir} is missing"})
            continue
        present = {str(p.relative_to(rdir)).replace(os.sep, "/") for p in rdir.rglob("*") if p.is_file()}
        for path, h in recorded.items():
            if path not in present:
                errors.append({"seq": e["seq"], "id": e["id"], "rule": "L13", "message": f"output {path} is missing"})
            elif file_hash(rdir / path) != h:
                errors.append({"seq": e["seq"], "id": e["id"], "rule": "L13",
                               "message": f"output {path} was changed after the run"})
        for path in sorted(present - set(recorded)):
            errors.append({"seq": e["seq"], "id": e["id"], "rule": "L13",
                           "message": f"file {path} was added to the run directory after the run"})
    return errors


def verify(lab, check_files=True):
    try:
        events = lab.read()
    except LabError as exc:
        return {"ok": False, "events": 0, "withheld": 0, "unchecked_types": [],
                "errors": [{"seq": None, "id": None, "rule": exc.rule, "message": exc.message}]}
    rep = verify_events(events)
    if check_files:
        rep["errors"] += verify_run_files(lab, events)
        rep["ok"] = not rep["errors"]
    return rep
