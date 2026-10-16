"""Timestamp anchoring of the lab record (lab record spec §7, §7.1) and rule L14."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .core import AGENTS_DIR, LabError, header_hash

CONFIG_FILE = "config.json"
ANCHORS_FILE = "anchors.jsonl"
DEFAULT_BRANCH = "claimset-lab"


# ---------------------------------------------------------------- configuration

def load_config(lab):
    p = lab.root / CONFIG_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LabError("config", f"{p} is not valid JSON: {exc}") from None


def save_config(lab, config):
    (lab.root / CONFIG_FILE).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def anchoring(lab):
    """The anchor configuration, or None if anchoring is not configured."""
    return load_config(lab).get("anchor")


def read_anchors(lab):
    p = lab.root / ANCHORS_FILE
    if not p.exists():
        return []
    out = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                raise LabError("L14", f"{ANCHORS_FILE} line {n} is not valid JSON") from None
    return out


def _int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else 0


def _agent_entries(entry):
    agents = entry.get("agents") if isinstance(entry, dict) else None
    return {k: v for k, v in agents.items() if isinstance(v, dict)} if isinstance(agents, dict) else {}


def covers(entry, event, owner=None):
    """True if the anchor `entry` covers `event`.

    `owner` is the actor whose open agent log the event is read from, if any. An event
    is covered by an anchor of the main log at or after its seq, or, if it was written
    to an agent log, by an anchor of that agent log at or after its seq there.
    """
    if not isinstance(entry, dict) or not isinstance(event, dict):
        return False
    agents = _agent_entries(entry)
    eid = str(event.get("id"))
    seq = _int(event.get("seq"))
    if owner and eid.startswith(owner + "."):
        return seq > 0 and _int((agents.get(owner) or {}).get("seq")) >= seq
    if seq > 0 and _int(entry.get("seq")) >= seq:
        return True
    o = event.get("origin")
    if isinstance(o, dict) and _int(o.get("seq")) > 0:
        return _int((agents.get(eid.split(".")[0]) or {}).get("seq")) >= _int(o.get("seq"))
    return False


def covered(lab, event, owner=None):
    """True if some anchor covers `event`."""
    return any(covers(a, event, owner) for a in read_anchors(lab))


# ---------------------------------------------------------------- git

def _git(args, cwd, env=None, check=True, input=None):
    out = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, input=input,
                         env=dict(os.environ, **(env or {})))
    if check and out.returncode != 0:
        raise LabError("anchor", f"git {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    return out


def _repo_top(lab):
    out = _git(["rev-parse", "--show-toplevel"], cwd=lab.root, check=False)
    if out.returncode != 0:
        raise LabError("anchor", f"{lab.root} is not inside a git repository; git anchoring needs one")
    return Path(out.stdout.strip())


def _lab_files(lab, top):
    files = [lab.log_path, lab.root / ANCHORS_FILE]
    agents = lab.root / "agents"
    if agents.is_dir():
        files += sorted(agents.glob("*.jsonl"))
    return [(f, f.resolve().relative_to(top.resolve()).as_posix()) for f in files if f.is_file()]


def _fetch(top, remote, branch):
    """Fetch the anchor branch into refs/claimset/remote-anchor; False if it does not exist yet."""
    fetch = _git(["fetch", "-q", remote, f"+refs/heads/{branch}:refs/claimset/remote-anchor"], cwd=top, check=False)
    if fetch.returncode != 0:
        if "couldn't find remote ref" in fetch.stderr or "not found" in fetch.stderr.lower():
            return False
        raise LabError("anchor", f"could not fetch {remote}/{branch}: {fetch.stderr.strip()}")
    return True


def _remote_file(top, rel, ref="refs/claimset/remote-anchor"):
    show = _git(["show", f"{ref}:{rel}"], cwd=top, check=False)
    if show.returncode != 0:
        return None
    return [l for l in show.stdout.splitlines() if l.strip()]


def _lines(path):
    return [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def compare(local_lines, remote_lines):
    """How a local log relates to its anchored copy: 'ahead' (or equal), 'behind' or 'diverged'.

    'behind' is normal in a working copy that has not caught up with the main log; 'diverged'
    means one of the two was rewritten.
    """
    n = min(len(local_lines), len(remote_lines))
    if local_lines[:n] != remote_lines[:n]:
        return "diverged"
    return "behind" if len(remote_lines) > len(local_lines) else "ahead"


def _first_difference(local_lines, remote_lines):
    for i, (r, l) in enumerate(zip(remote_lines, local_lines), start=1):
        if r != l:
            return i
    return min(len(local_lines), len(remote_lines)) + 1


def check_prefix(local_lines, remote_lines):
    """None if remote is a prefix of local, else a description of the first difference."""
    state = compare(local_lines, remote_lines)
    if state == "ahead":
        return None
    if state == "behind":
        return (f"the remote log has {len(remote_lines)} events but the local log only {len(local_lines)}: "
                f"events were removed locally, or this copy has not caught up")
    return (f"event {_first_difference(local_lines, remote_lines)} differs from the anchored copy: "
            f"the log was rewritten after anchoring")


def _log_files(lab, top):
    """(path, path in the repository, name) of the main and agent logs."""
    return [(p, rel, rel.split("/")[-1] if p == lab.log_path else f"{AGENTS_DIR}/{p.name}")
            for p, rel in _lab_files(lab, top) if p.name != ANCHORS_FILE]


def anchor_git(lab, remote="origin", branch=DEFAULT_BRANCH):
    """Commit the lab's logs to `branch` and push it to `remote`. Returns the anchor entry.

    The commit starts from the branch as it is on the remote. Each local log that extends its
    anchored copy replaces it; a log that is behind (another working copy has added to it) is
    kept as anchored; a log that differs is refused. Anchor receipts from all copies are kept.
    """
    events = lab.read()
    if not events:
        raise LabError("anchor", "the log is empty")
    local_errors = verify_anchors(lab, events)
    if local_errors:
        raise LabError("L14", f"refusing to anchor: {local_errors[0]['message']}")
    top = _repo_top(lab)
    exists = _fetch(top, remote, branch)
    parent = _git(["rev-parse", "refs/claimset/remote-anchor"], cwd=top).stdout.strip() if exists else None

    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(["read-tree", "refs/claimset/remote-anchor" if exists else "--empty"], cwd=top, env=env)
        for path, rel, name in _log_files(lab, top):
            remote_lines = _remote_file(top, rel) if exists else None
            if remote_lines is not None:
                state = compare(_lines(path), remote_lines)
                if state == "diverged":
                    raise LabError("L14", f"refusing to anchor: {name}: "
                                          f"{check_prefix(_lines(path), remote_lines)}")
                if state == "behind":
                    continue
            blob = _git(["hash-object", "-w", str(path)], cwd=top).stdout.strip()
            _git(["update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}"], cwd=top, env=env)
        anchors_path = lab.root / ANCHORS_FILE
        rel = anchors_path.resolve().relative_to(top.resolve()).as_posix()
        local_anchors = _lines(anchors_path) if anchors_path.exists() else []
        remote_anchors = (_remote_file(top, rel) or []) if exists else []
        union = remote_anchors + [l for l in local_anchors if l not in set(remote_anchors)]
        if union:
            blob = _git(["hash-object", "-w", "--stdin"], cwd=top, input="\n".join(union) + "\n").stdout.strip()
            _git(["update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}"], cwd=top, env=env)
        tree = _git(["write-tree"], cwd=top, env=env).stdout.strip()
    head = events[-1]
    ident = {"GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "claimset"),
             "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "claimset@localhost"),
             "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "claimset"),
             "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "claimset@localhost")}
    args = ["commit-tree", tree, "-m", f"claimset lab: anchor seq {head['seq']} ({head['id']})"]
    if parent:
        args[2:2] = ["-p", parent]
    commit = _git(args, cwd=top, env=ident).stdout.strip()
    _git(["update-ref", f"refs/heads/{branch}", commit], cwd=top)
    push = _git(["push", "-q", remote, f"refs/heads/{branch}:refs/heads/{branch}"], cwd=top, check=False)
    if push.returncode != 0:
        raise LabError("anchor", f"push to {remote}/{branch} was rejected: {push.stderr.strip()}. "
                                 f"The remote branch changed in a way the tool did not make.")
    entry = {"seq": head["seq"], "head_hash": header_hash(head), "service": "git_remote",
             "receipt": {"remote": remote, "branch": branch, "commit": commit}, "at": lab.clock()}
    agents = {}
    for actor in lab.agent_logs():
        events = lab.read_agent(actor)
        if events:
            agents[actor] = {"seq": events[-1]["seq"], "head_hash": header_hash(events[-1])}
    if agents:
        entry["agents"] = agents
    with open(lab.root / ANCHORS_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return entry


def anchor(lab):
    """Anchor using the configured service. Raises LabError if not configured or if it fails."""
    cfg = anchoring(lab)
    if not cfg:
        raise LabError("anchor", "anchoring is not configured; run `claimset lab anchor --git` once")
    if cfg.get("service") != "git_remote":
        raise LabError("anchor", f"anchoring service '{cfg.get('service')}' is not supported by this version")
    return anchor_git(lab, cfg.get("remote", "origin"), cfg.get("branch", DEFAULT_BRANCH))


def ensure_covered(lab, event, owner=None):
    """Make sure `event` is covered by an anchor, anchoring now if needed."""
    if covered(lab, event, owner):
        return None
    return anchor(lab)


# ---------------------------------------------------------------- verification

def _check_position(lines_or_events, pos, n, log=None, what="the log"):
    """L14 for one anchored position {seq, head_hash} against a list of parsed events."""
    seq = pos.get("seq") if isinstance(pos, dict) else None
    events = lines_or_events
    extra = {"log": log} if log else {}
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or seq > len(events):
        return [dict({"seq": seq, "id": None, "rule": "L14",
                      "message": f"anchor {n} names seq {seq} of {what}, which is not there: events were removed"},
                     **extra)]
    e = events[seq - 1]
    if header_hash(e) != pos.get("head_hash"):
        return [dict({"seq": seq, "id": e.get("id") if isinstance(e, dict) else None, "rule": "L14",
                      "message": f"anchor {n} does not match {what}: it was rewritten after it was anchored"},
                     **extra)]
    return []


def verify_anchors(lab, events):
    """Rule L14 against the local anchors file."""
    errors = []
    try:
        anchors = read_anchors(lab)
    except LabError as exc:
        return [{"seq": None, "id": None, "rule": exc.rule, "message": exc.message}]
    for n, a in enumerate(anchors, start=1):
        if not isinstance(a, dict):
            errors.append({"seq": None, "id": None, "rule": "L14", "message": f"anchor {n} is not an object"})
            continue
        errors += _check_position(events, a, n)
        agents = a.get("agents")
        if agents is not None and not isinstance(agents, dict):
            errors.append({"seq": None, "id": None, "rule": "L14", "message": f"anchor {n}: 'agents' is not an object"})
            continue
        for actor, pos in sorted((agents or {}).items()):
            log = lab.agent_log_name(actor)
            try:
                agent = lab.read_agent(actor)
            except LabError:
                agent = []
            errors += _check_position(agent, pos, n, log, log)
    return errors


def verify_confirmatory_anchoring(lab, events, owner=None):
    """Rule L5 with anchoring: a confirmatory run's plan was anchored before the run started.

    `events` is the main log, or, with `owner`, the view of that actor's open agent log.
    """
    if not anchoring(lab):
        return []
    try:
        anchors = read_anchors(lab)
    except LabError:
        return []  # reported by verify_anchors
    by_id = {e.get("id"): e for e in events if isinstance(e, dict)}
    errors = []
    for e in events:
        if not isinstance(e, dict):
            continue
        b = e.get("body") or {}
        if e.get("type") != "run" or b.get("label") != "confirmatory" or b.get("withheld"):
            continue
        if owner and not str(e.get("id")).startswith(owner + "."):
            continue
        plan = by_id.get(b.get("plan"))
        if plan is None:
            continue
        started = b.get("started_at") if isinstance(b.get("started_at"), str) else ""
        ok = any(covers(a, plan, owner) and isinstance(a.get("at"), str) and a["at"] <= started for a in anchors)
        if not ok:
            errors.append({"seq": e["seq"], "id": e["id"], "rule": "L5",
                           "message": f"plan {plan['id']} was not anchored before this confirmatory run started"})
    return errors


def _parsed(lines):
    out = []
    for l in lines or []:
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            out.append(None)
    return out


def verify_remote(lab):
    """Check the local logs against the anchor branch on the remote (spec §7.1).

    Returns {"errors", "notes"}: notes report logs that are behind the anchored copy, which is
    expected in a working copy that has not caught up.
    """
    def fail(msg, **kw):
        return dict({"seq": None, "id": None, "rule": "L14", "message": msg}, **kw)

    cfg = anchoring(lab)
    if not cfg:
        return {"errors": [fail("anchoring is not configured")], "notes": []}
    top = _repo_top(lab)
    remote, branch = cfg.get("remote", "origin"), cfg.get("branch", DEFAULT_BRANCH)
    try:
        exists = _fetch(top, remote, branch)
    except LabError as exc:
        return {"errors": [fail(exc.message)], "notes": []}
    if not exists:
        return {"errors": [fail(f"{remote}/{branch} does not exist")], "notes": []}
    errors, notes = [], []
    for path, rel, name in _log_files(lab, top):
        remote_lines = _remote_file(top, rel)
        if remote_lines is None:
            continue
        local_lines = _lines(path)
        state = compare(local_lines, remote_lines)
        if state == "diverged":
            errors.append(fail(f"{name}: {check_prefix(local_lines, remote_lines)}"))
        elif state == "behind":
            notes.append(f"{name} is {len(remote_lines) - len(local_lines)} events behind {remote}/{branch}")
    main_rel = lab.log_path.resolve().relative_to(top.resolve()).as_posix()
    try:
        anchors = read_anchors(lab)
    except LabError:
        anchors = []
    for n, a in enumerate(anchors, start=1):
        commit = (a.get("receipt") or {}).get("commit") if isinstance(a, dict) else None
        if not isinstance(commit, str) or not commit:
            continue
        on_branch = _git(["merge-base", "--is-ancestor", commit, "refs/claimset/remote-anchor"], cwd=top, check=False)
        if on_branch.returncode != 0:
            errors.append(fail(f"anchor {n}: commit {commit[:12]} is not on {remote}/{branch}: "
                               f"the branch was rewritten", seq=a.get("seq")))
            continue
        errors += _check_position(_parsed(_remote_file(top, main_rel, commit)), a, n,
                                  what=f"lab.jsonl in commit {commit[:12]}")
        agents = a.get("agents") if isinstance(a.get("agents"), dict) else {}
        for actor, pos in sorted(agents.items()):
            name = lab.agent_log_name(actor)
            rel = (lab.root / name).resolve().relative_to(top.resolve()).as_posix()
            errors += _check_position(_parsed(_remote_file(top, rel, commit)), pos, n, name,
                                      f"{name} in commit {commit[:12]}")
    return {"errors": errors, "notes": notes}
