"""The lab record: an append-only, hash-chained event log (lab record spec v0.4).

This module reads, writes and verifies `lab/lab.jsonl` and the per-agent logs
`lab/agents/<actor>.jsonl` (§3.1). Everything that writes goes through
`Lab.append` or `Lab.merge`, which take the lock, check the new events against
the log so far and refuse them if they break a rule.
"""

import hashlib
import heapq
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
               "deviation", "merge"}

#: Event types that are written only to the main log, never to an agent log (§3.1).
MAIN_ONLY = {"init", "actor", "merge"}
AGENTS_DIR = "agents"

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
ID_RE = re.compile(r"^(?:(AC\d+)\.)?([A-Z]+)(\d+)$")
AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
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
    if not isinstance(event, dict):
        return event
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
    """Indexes of the log so far, used to check each new event against it.

    `owner` is None for the main log. For an agent log it is the agent's actor ID:
    the state then holds the main log up to where the agent started, followed by
    the agent's own events, and new IDs carry the actor prefix (§3.1).
    """

    def __init__(self, owner=None):
        self.owner = owner
        self.base = None
        self.agent_events = []
        self.merged = {}
        self._last_merge = None
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
        n = self.max_num.get((self.owner, prefix), 0) + 1
        return f"{self.owner}.{prefix}{n}" if self.owner else f"{prefix}{n}"

    def head(self):
        return self.events[-1] if self.events else None

    # -- update
    def apply(self, event):
        self.events.append(event)
        if not (isinstance(event, dict) and isinstance(event.get("id"), str) and isinstance(event.get("type"), str)):
            return
        eid = event["id"]
        self.by_id[eid] = event
        m = ID_RE.match(eid)
        if m:
            key, n = (m.group(1), m.group(2)), int(m.group(3))
            self.max_num[key] = max(self.max_num.get(key, 0), n)
        sup = event.get("supersedes")
        if isinstance(sup, str) and sup:
            self.superseded_by[sup] = eid
            self.root_of[eid] = self.root(sup)
        if event["type"] == "actor":
            self.actors.add(eid)
        body = event.get("body")
        if event["type"] == "merge":
            self._last_merge = eid
            if isinstance(body, dict) and not is_withheld(event) and isinstance(body.get("agents"), list):
                for a in body["agents"]:
                    if isinstance(a, str):
                        self.merged[a] = eid
        if isinstance(event.get("origin"), dict) and m and m.group(1):
            # A merged event marks its agent log as merged even if the merge event is unreadable.
            self.merged.setdefault(m.group(1), self._last_merge or "an earlier merge")

    # -- checks for a new event (rules L6-L12 and body schemas)
    def check(self, etype, eid, by, body, supersedes):
        self.check_header(etype, eid, by, supersedes)
        if not isinstance(body, dict):
            raise LabError("schema", "body must be an object")
        checker = BODY_CHECKS.get(etype)
        if checker:
            checker(self, body, by, eid)

    def check_header(self, etype, eid, by, supersedes):
        """The checks that need only the header; they apply to withheld events too."""
        if etype not in PREFIX and etype != "init":
            raise LabError("L12", f"unknown event type '{etype}'")
        if self.owner is not None:
            if etype in MAIN_ONLY:
                raise LabError("L10", f"{etype} events are written to the main log, not to an agent log")
            if by != self.owner:
                raise LabError("L10", f"the agent log of {self.owner} is written only by {self.owner}")
            if supersedes is not None and not str(supersedes).startswith(self.owner + "."):
                raise LabError("L10", f"an agent log corrects only its own events; correct {supersedes} "
                                      f"in the main log after this log is merged")
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


def final_statuses(st):
    """{hypothesis root ID: its final status event}.

    The final status is the last status event for the hypothesis, a correction standing in
    its original's place: correcting an earlier status does not make it later.
    """
    out = {}
    for e in st.events:
        if e.get("type") != "status" or is_withheld(e) or e["id"] in st.superseded_by:
            continue
        target = e["body"].get("target") if isinstance(e.get("body"), dict) else None
        if not target:
            continue
        root = st.root(target)
        place = st.get(st.root(e["id"]))["seq"]
        if root not in out or place > out[root][0]:
            out[root] = (place, e)
    return {k: v[1] for k, v in out.items()}


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
            hint = ""
            if state.owner:
                hint = (f" visible to the agent log of {state.owner}, which sees the main log up to seq "
                        f"{state.base} and its own events; other events can be referred to after merging")
            raise LabError("L12", f"{etype}: '{field}' names '{i}', which is not an earlier event{hint}")
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
        if is_withheld(plan):
            return  # the plan's kind is withheld (§8); the run was checked when it was written
        if plan["body"].get("kind") != "confirmatory":
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


def check_merge(state, body, by, eid):
    agents = _req(body, "agents", "merge")
    if not isinstance(agents, list) or not all(isinstance(a, str) for a in agents):
        raise LabError("schema", "merge: 'agents' must be a list of actor IDs")
    for a in agents:
        if a not in state.actors:
            raise LabError("L12", f"merge: '{a}' is not a registered actor")
        if a in state.merged:
            raise LabError("L10", f"merge: the log of {a} was already merged by {state.merged[a]}")
    n = body.get("events_merged")
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise LabError("schema", "merge: 'events_merged' must be a non-negative integer")


BODY_CHECKS = {
    "init": check_init, "actor": check_actor, "question": check_question,
    "hypothesis": check_hypothesis, "status": check_status, "assumption": check_assumption,
    "decision": check_decision, "check": check_check, "artefact": check_artefact, "note": check_note,
    "plan": check_plan, "approval": check_approval, "run": check_run, "result": check_result,
    "deviation": check_deviation, "merge": check_merge,
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
        # Receipts from several working copies are combined when their branches are merged (§7.1).
        (lab.root / ".gitattributes").write_text("anchors.jsonl merge=union\n", encoding="utf-8")
        now = clock()
        lab.append("init", {"lab_spec_version": LAB_SPEC_VERSION, "working_title": title,
                            "repository": repository, "started_at": now}, by=None)
        return lab

    def exists(self):
        return self.log_path.exists()

    @staticmethod
    def _read_path(path, label):
        events = []
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise LabError("L2", f"{label} line {n} is not valid JSON: {exc}") from None
        return events

    def read(self):
        """All events in the main log, parsed. Raises LabError on unreadable lines."""
        if not self.log_path.exists():
            raise LabError("init", f"no lab record at {self.log_path}; run `claimset lab init` first")
        return self._read_path(self.log_path, "lab.jsonl")

    def state(self):
        """State of the main log."""
        st = State()
        for e in self.read():
            st.apply(e)
        return st

    # -- agent logs (§3.1)
    def agent_log_path(self, actor):
        return self.root / AGENTS_DIR / f"{actor}.jsonl"

    def agent_log_name(self, actor):
        return f"{AGENTS_DIR}/{actor}.jsonl"

    def agent_logs(self):
        """Actor IDs that have an agent log, in actor order."""
        d = self.root / AGENTS_DIR
        names = [p.stem for p in d.glob("*.jsonl")] if d.is_dir() else []
        return sorted((n for n in names if re.match(r"^AC\d+$", n)), key=lambda a: int(a[2:]))

    def read_agent(self, actor):
        path = self.agent_log_path(actor)
        if not path.exists():
            raise LabError("L10", f"{actor} has no agent log")
        return self._read_path(path, self.agent_log_name(actor))

    def view(self, by=None, main=None):
        """The state a writer sees: its open agent log on top of the main log where it started,
        or the main log if `by` has no open agent log."""
        main = self.read() if main is None else main
        st = State()
        for e in main:
            st.apply(e)
        if by is None or by in st.merged or not self.agent_log_path(by).exists():
            return st
        agent = self.read_agent(by)
        base = agent_base(main, agent)
        if base is None:
            raise LabError("L10", f"the agent log of {by} does not start from an event of the main log")
        v = State(owner=by)
        for e in main[:base]:
            v.apply(e)
        for e in agent:
            v.apply(e)
        v.base, v.agent_events = base, agent
        return v

    def open_agent_logs(self, main=None):
        st = self.view(main=main)
        return [a for a in self.agent_logs() if a not in st.merged]

    def start_agent(self, actor):
        """Start an agent log for `actor`; its writes go there until it is merged."""
        with self._lock():
            st = self.state()
            if actor not in st.actors:
                raise LabError("L8", f"'{actor}' is not a registered actor; register it in the main log first")
            if actor in st.merged:
                raise LabError("L10", f"the log of {actor} was merged by {st.merged[actor]}; an agent log is used "
                                      f"once, so register a new actor for new concurrent work")
            path = self.agent_log_path(actor)
            if path.exists():
                raise LabError("L10", f"{actor} already has an agent log at {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path

    # -- writing
    def append(self, etype, body, by, supersedes=None):
        """Check and append one event to the writer's log; return it. Raises LabError if refused.

        Events by an actor with an open agent log go to that log (§3.1); all others to the main log.
        """
        with self._lock():
            main = self.read() if self.log_path.exists() else []
            st = self.view(by, main) if main else State()
            eid = st.next_id(etype)
            st.check(etype, eid, by, body, supersedes)
            head = st.head()
            path = self.agent_log_path(by) if st.owner else self.log_path
            seq = len(st.agent_events) + 1 if st.owner else len(st.events) + 1
            event = {
                "seq": seq,
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
            self._write_lines(path, [event])
            return event

    def merge(self, by, actors=None):
        """Merge agent logs into the main log (§3.1). Returns the merge event.

        The merge event is written first, followed by the merged events in order of `at`
        (each agent's own order kept; ties broken by actor, then seq). Either all of them
        are written or none.
        """
        with self._lock():
            main = self.read()
            st = State()
            for e in main:
                st.apply(e)
            available = [a for a in self.agent_logs() if a not in st.merged]
            actors = list(actors) if actors else available
            if not actors:
                raise LabError("merge", "there are no unmerged agent logs")
            logs = {}
            for a in actors:
                if a in st.merged:
                    raise LabError("L10", f"the log of {a} was already merged by {st.merged[a]}")
                events = self.read_agent(a)
                problems = verify_agent_log(main, a, events)
                if problems:
                    p = problems[0]
                    raise LabError("L10", f"the log of {a} does not verify (seq {p['seq']} {p['id'] or ''}: "
                                          f"[{p['rule']}] {p['message']}); nothing was merged")
                logs[a] = events
            actors = sorted(set(actors), key=lambda a: int(a[2:]))
            streams = [[(e["at"], int(a[2:]), e["seq"], a, i) for i, e in enumerate(logs[a])] for a in actors]
            order = [(a, logs[a][i]) for _, _, _, a, i in heapq.merge(*streams)]

            body = {"agents": actors, "events_merged": len(order)}
            mid = st.next_id("merge")
            st.check("merge", mid, by, body, None)
            out = [{"seq": len(st.events) + 1, "id": mid, "type": "merge", "at": self.clock(), "by": by,
                    "prev": header_hash(st.head()), "content_hash": content_hash(body), "supersedes": None,
                    "origin": None, "body": body}]
            st.apply(out[0])
            for a, e in order:
                try:
                    st.check(e["type"], e["id"], e["by"], e["body"], e["supersedes"])
                except LabError as exc:
                    raise LabError(exc.rule, f"cannot merge {e['id']}: {exc.message}; nothing was merged") from None
                m = {"seq": len(st.events) + 1, "id": e["id"], "type": e["type"], "at": e["at"], "by": e["by"],
                     "prev": header_hash(st.head()), "content_hash": e["content_hash"],
                     "supersedes": e["supersedes"],
                     "origin": {"log": self.agent_log_name(a), "seq": e["seq"], "header_hash": header_hash(e)},
                     "body": e["body"]}
                st.apply(m)
                out.append(m)
            self._write_lines(self.log_path, out)
            return out[0]

    @staticmethod
    def _write_lines(path, events):
        data = "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in events)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())

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

def agent_base(main, agent):
    """Number of main-log events an agent log starts from (its first event's prev), or None."""
    if not agent:
        return len(main)
    first_prev = agent[0].get("prev") if isinstance(agent[0], dict) else None
    for i in range(len(main), 0, -1):
        if header_hash(main[i - 1]) == first_prev:
            return i
    return None


def verify_events(events, base=(), owner=None, log=None):
    """Check a sequence of parsed events against rules L2 and L8-L12 and the body checks.

    For an agent log, `owner` is its actor and `base` the main-log events it started from.
    Returns a report: {"ok", "events", "withheld", "unchecked_types", "errors": [{seq, id, rule, message}]}.
    """
    errors = []
    st = State(owner=owner)
    for e in base:
        st.apply(e)
    st.base = len(base)
    prev = header_hash(base[-1]) if base else GENESIS_PREV
    withheld = 0
    unchecked = set()
    group = None  # the merge being read: {"id", "agents", "left"}

    def close_group(seq):
        if not group:
            return
        if group["left"] > 0:
            errors.append({"seq": seq, "id": group["id"], "rule": "L10",
                           "message": f"{group['id']} announces {group['n']} merged events but "
                                      f"{group['n'] - group['left']} follow it"})
        errors.extend(_merge_order_errors(group))

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
            prev = header_hash(e)
            continue
        if e["seq"] != i:
            err("L2", f"seq is {e['seq']}, expected {i}: an event was removed, added or reordered")
        if e["prev"] != prev:
            err("L2", "prev does not match the previous event's header: the chain is broken here")
        prev = header_hash(e)
        malformed = [k for k, ok in (
            ("seq", isinstance(e["seq"], int) and not isinstance(e["seq"], bool)),
            ("id", isinstance(eid, str)), ("type", isinstance(e["type"], str)),
            ("at", isinstance(e["at"], str) and AT_RE.match(e["at"])),
            ("by", e["by"] is None or isinstance(e["by"], str)),
            ("supersedes", e["supersedes"] is None or isinstance(e["supersedes"], str)),
            ("body", isinstance(e["body"], dict))) if not ok]
        if malformed:
            err("L2", f"malformed fields {malformed} (at is a UTC time like 2026-10-15T10:00:00Z)")
            st.apply(e)
            continue
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
        ns = m.group(1) if m else None
        if etype == "init":
            if eid != INIT_ID:
                err("L12", "the init event's ID is INIT")
        elif expected_prefix is None:
            err("L12", f"unknown event type '{etype}'")
        elif not m or m.group(2) != expected_prefix:
            err("L12", f"ID '{eid}' does not have the prefix {expected_prefix} of type {etype}")
        if eid in st.by_id:
            err("L12", f"duplicate ID '{eid}'")

        origin = e.get("origin")
        if owner is not None:
            if ns != owner:
                err("L10", f"events in the agent log of {owner} have IDs starting {owner}.")
            if origin is not None:
                err("L10", "events in an agent log have origin null")
        else:
            if origin is None:
                close_group(e["seq"])
                group = None
                if ns is not None:
                    err("L10", f"'{eid}' has an agent-log ID but no origin: only merged events carry one")
            else:
                ok = (isinstance(origin, dict) and isinstance(origin.get("seq"), int)
                      and HASH_RE.match(str(origin.get("header_hash", ""))) and ns is not None
                      and origin.get("log") == f"{AGENTS_DIR}/{ns}.jsonl")
                if not ok:
                    err("L10", "origin must be {log: agents/<actor>.jsonl, seq, header_hash} matching the ID")
                if group is None or group["left"] <= 0 or ns not in group["agents"]:
                    err("L10", f"merged event '{eid}' does not belong to the merge before it")
                else:
                    group["left"] -= 1
                    group["items"].append((e, ns, origin.get("seq") if ok else None))

        if etype == "merge" and is_withheld(e):
            err("L10", "merge events are never withheld: they hold no content, only which logs were merged")
        if is_withheld(e) and etype in PREFIX:
            try:
                st.check_header(etype, eid, e["by"], e["supersedes"])
            except LabError as exc:
                err(exc.rule, exc.message)
        if is_withheld(e) or etype not in IMPLEMENTED:
            if etype not in IMPLEMENTED and etype in PREFIX:
                unchecked.add(etype)
            st.apply(e)
            continue
        try:
            st.check(etype, eid, e["by"], e["body"], e["supersedes"])
        except LabError as exc:
            err(exc.rule, exc.message)
        if etype == "merge" and owner is None:
            n = e["body"].get("events_merged") if isinstance(e["body"].get("events_merged"), int) else 0
            agents = e["body"].get("agents") if isinstance(e["body"].get("agents"), list) else []
            group = {"id": eid, "agents": {a for a in agents if isinstance(a, str)}, "left": n, "n": n, "items": []}
        st.apply(e)
    if owner is None:
        close_group(len(events))
    if log:
        for x in errors:
            x["log"] = log
    return {"ok": not errors, "events": len(events), "withheld": withheld,
            "unchecked_types": sorted(unchecked), "errors": errors}


def _actor_num(actor):
    return int(actor[2:]) if re.match(r"^AC\d+$", str(actor)) else 0


def _merge_order_key(e, actor, seq):
    return (e.get("at") or "", _actor_num(actor), seq)


def _merge_order_errors(group):
    """The merged events of one merge: each log complete from seq 1 in its own order, interleaved as §3.1 says."""
    items = group["items"]
    if any(not isinstance(s, int) for _, _, s in items):
        return []  # reported already as a malformed origin
    errors, streams = [], {}
    for e, actor, s in items:
        streams.setdefault(actor, []).append((e, s))
    for actor, xs in streams.items():
        if [s for _, s in xs] != list(range(1, len(xs) + 1)):
            errors.append({"seq": xs[0][0]["seq"], "id": group["id"], "rule": "L10",
                           "message": f"{group['id']} does not merge the log of {actor} whole and in its own order"})
    if errors:
        return errors
    expected = [e["id"] for _, _, _, e in heapq.merge(
        *[[(*_merge_order_key(e, a, s), e) for e, s in xs] for a, xs in sorted(
            streams.items(), key=lambda kv: _actor_num(kv[0]))], key=lambda t: t[:3])]
    if expected != [e["id"] for e, _, _ in items]:
        errors.append({"seq": items[0][0]["seq"], "id": group["id"], "rule": "L10",
                       "message": f"the events merged by {group['id']} are not in the order of §3.1 "
                                  f"(by time, each agent's own order kept)"})
    return errors


def verify_agent_log(main, actor, events, log=None):
    """Errors in one agent log, checked on its own (rule L10 and L2-L12 within the log)."""
    log = log or f"{AGENTS_DIR}/{actor}.jsonl"
    base = agent_base(main, events)
    if base is None:
        return [{"seq": 1, "id": None, "rule": "L10", "log": log,
                 "message": "the first event's prev is not the header of any main-log event"}]
    return verify_events(events, base=main[:base], owner=actor, log=log)["errors"]


def verify_agents(lab, main, check_files=True):
    """Rule L10 across logs: every agent log verifies, and merged events match their agent log."""
    errors = []
    st = State()
    for e in main:
        if isinstance(e, dict):
            st.apply(e)
    merged_events = {}
    for e in main:
        o = e.get("origin") if isinstance(e, dict) else None
        if isinstance(o, dict):
            merged_events.setdefault(str(e.get("id")).split(".")[0], []).append(e)
    present = set(lab.agent_logs())
    for actor in sorted(set(st.merged) - present, key=_actor_num):
        errors.append({"seq": None, "id": None, "rule": "L10", "log": lab.agent_log_name(actor),
                       "message": f"the log of {actor} was merged by {st.merged[actor]} but is missing"})
    for actor in sorted(present, key=lambda a: int(a[2:])):
        log = lab.agent_log_name(actor)
        try:
            events = lab.read_agent(actor)
        except LabError as exc:
            errors.append({"seq": None, "id": None, "rule": exc.rule, "log": log, "message": exc.message})
            continue
        if actor not in st.actors:
            errors.append({"seq": None, "id": None, "rule": "L10", "log": log,
                           "message": f"{actor} is not a registered actor, so it cannot have an agent log"})
        errors += verify_agent_log(main, actor, events, log)
        if actor in st.merged:
            done = merged_events.get(actor, [])
            merge_event = st.get(st.merged[actor])
            base = agent_base(main, events) if events else None
            if merge_event is not None and base is not None and merge_event.get("seq", 0) <= base:
                errors.append({"seq": merge_event.get("seq"), "id": merge_event.get("id"), "rule": "L10",
                               "message": f"{merge_event.get('id')} comes before the main-log event "
                                          f"the log of {actor} started from"})
            for m in done:
                o = m["origin"]
                if not isinstance(o.get("seq"), int):
                    continue  # reported by verify_events
                a = events[o["seq"] - 1] if 1 <= o["seq"] <= len(events) else None
                same = a is not None and header_hash(a) == o.get("header_hash") and all(
                    a.get(k) == m.get(k) for k in ("id", "type", "at", "by", "content_hash", "supersedes"))
                if not same:
                    errors.append({"seq": m["seq"], "id": m["id"], "rule": "L10",
                                   "message": f"merged event does not match event {o['seq']} of {log}"})
            if len(events) != len(done):
                errors.append({"seq": None, "id": None, "rule": "L10", "log": log,
                               "message": f"{len(events)} events in the agent log but {len(done)} were merged"
                                          f" by {st.merged[actor]}: the log changed after it was merged"})
        elif check_files:
            for x in verify_run_files(lab, events):
                x["log"] = log
                errors.append(x)
    return errors


def verify_run_files(lab, events):
    """Rule L13: each run directory holds exactly the files recorded in the run's outputs."""
    errors = []
    for e in events:
        if not isinstance(e, dict) or e.get("type") != "run" or is_withheld(e) or not isinstance(e.get("body"), dict):
            continue
        if not isinstance(e.get("id"), str) or not ID_RE.match(e["id"]):
            continue
        outputs = e["body"].get("outputs")
        if not isinstance(outputs, list) or not all(isinstance(o, dict) and isinstance(o.get("path"), str)
                                                    for o in outputs):
            continue  # reported by the run body check
        rdir = lab.root / "runs" / e["id"]
        recorded = {o["path"]: o.get("hash") for o in outputs}
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


def verify(lab, check_files=True, remote=False):
    try:
        events = lab.read()
    except LabError as exc:
        return {"ok": False, "events": 0, "withheld": 0, "unchecked_types": [],
                "errors": [{"seq": None, "id": None, "rule": exc.rule, "message": exc.message}]}
    from .anchor import verify_anchors, verify_confirmatory_anchoring, verify_remote

    rep = verify_events(events)
    if check_files:
        rep["errors"] += verify_run_files(lab, events)
    rep["errors"] += verify_anchors(lab, events)
    rep["errors"] += verify_confirmatory_anchoring(lab, events)
    rep["errors"] += verify_agents(lab, events, check_files)
    st = State()
    for e in events:
        if isinstance(e, dict):
            st.apply(e)
    rep["agents"] = {}
    for a in lab.agent_logs():
        try:
            n = len(lab.read_agent(a))
        except LabError:
            n = None
        rep["agents"][a] = {"events": n, "merged_by": st.merged.get(a)}
        if a not in st.merged:
            try:
                view = lab.view(a, events)
            except LabError:
                continue
            for x in verify_confirmatory_anchoring(lab, view.events, owner=a):
                x["log"] = lab.agent_log_name(a)
                rep["errors"].append(x)
    rep["notes"] = []
    if remote:
        r = verify_remote(lab)
        rep["errors"] += r["errors"]
        rep["notes"] += r["notes"]
    rep["ok"] = not rep["errors"]
    return rep
