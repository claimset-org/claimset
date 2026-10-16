"""Timestamp anchoring of the lab record (lab record spec §7, §7.1) and rule L14."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .core import LabError, header_hash

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


def covered(lab, seq):
    """True if some anchor covers the event at `seq`."""
    return any(a.get("seq", 0) >= seq for a in read_anchors(lab))


# ---------------------------------------------------------------- git

def _git(args, cwd, env=None, check=True):
    out = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
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


def _remote_log(lab, top, remote, branch):
    """Lines of lab.jsonl on the remote anchor branch, or None if the branch does not exist there."""
    fetch = _git(["fetch", "-q", remote, f"+refs/heads/{branch}:refs/claimset/remote-anchor"], cwd=top, check=False)
    if fetch.returncode != 0:
        if "couldn't find remote ref" in fetch.stderr or "not found" in fetch.stderr.lower():
            return None
        raise LabError("anchor", f"could not fetch {remote}/{branch}: {fetch.stderr.strip()}")
    rel = lab.log_path.resolve().relative_to(top.resolve()).as_posix()
    show = _git(["show", f"refs/claimset/remote-anchor:{rel}"], cwd=top, check=False)
    if show.returncode != 0:
        return []
    return show.stdout.splitlines()


def check_prefix(local_lines, remote_lines):
    """None if remote is a prefix of local, else a description of the first difference."""
    if len(remote_lines) > len(local_lines):
        return (f"the remote log has {len(remote_lines)} events but the local log only {len(local_lines)}: "
                f"events were removed locally")
    for i, (r, l) in enumerate(zip(remote_lines, local_lines), start=1):
        if r != l:
            return f"event {i} differs from the anchored copy: the local log was rewritten after anchoring"
    return None


def anchor_git(lab, remote="origin", branch=DEFAULT_BRANCH):
    """Commit the lab's logs to `branch` and push it to `remote`. Returns the anchor entry."""
    events = lab.read()
    if not events:
        raise LabError("anchor", "the log is empty")
    top = _repo_top(lab)
    local_lines = [l for l in lab.log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    remote_lines = _remote_log(lab, top, remote, branch)
    if remote_lines is not None:
        problem = check_prefix(local_lines, remote_lines)
        if problem:
            raise LabError("L14", f"refusing to anchor: {problem}")
    parent = None
    if remote_lines is not None:
        parent = _git(["rev-parse", "refs/claimset/remote-anchor"], cwd=top).stdout.strip()

    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(["read-tree", "--empty"], cwd=top, env=env)
        for path, rel in _lab_files(lab, top):
            blob = _git(["hash-object", "-w", str(path)], cwd=top).stdout.strip()
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


def ensure_covered(lab, seq):
    """Make sure the event at `seq` is covered by an anchor, anchoring now if needed."""
    if covered(lab, seq):
        return None
    return anchor(lab)


# ---------------------------------------------------------------- verification

def verify_anchors(lab, events):
    """Rule L14 against the local anchors file."""
    errors = []
    try:
        anchors = read_anchors(lab)
    except LabError as exc:
        return [{"seq": None, "id": None, "rule": exc.rule, "message": exc.message}]
    for n, a in enumerate(anchors, start=1):
        seq = a.get("seq")
        if not isinstance(seq, int) or seq < 1 or seq > len(events):
            errors.append({"seq": seq, "id": None, "rule": "L14",
                           "message": f"anchor {n} names seq {seq}, which is not in the log: events were removed"})
        elif header_hash(events[seq - 1]) != a.get("head_hash"):
            errors.append({"seq": seq, "id": events[seq - 1].get("id"), "rule": "L14",
                           "message": f"anchor {n} does not match the log: the log was rewritten after it was anchored"})
    return errors


def verify_confirmatory_anchoring(lab, events):
    """Rule L5 with anchoring: a confirmatory run's plan was anchored before the run started."""
    if not anchoring(lab):
        return []
    anchors = read_anchors(lab)
    by_id = {e.get("id"): e for e in events}
    errors = []
    for e in events:
        b = e.get("body") or {}
        if e.get("type") != "run" or b.get("label") != "confirmatory" or b.get("withheld"):
            continue
        plan = by_id.get(b.get("plan"))
        if plan is None:
            continue
        ok = any(a.get("seq", 0) >= plan["seq"] and a.get("at", "") <= b.get("started_at", "") for a in anchors)
        if not ok:
            errors.append({"seq": e["seq"], "id": e["id"], "rule": "L5",
                           "message": f"plan {plan['id']} was not anchored before this confirmatory run started"})
    return errors


def verify_remote(lab):
    """Check the local log against the anchor branch on the remote (spec §7.1)."""
    cfg = anchoring(lab)
    if not cfg:
        return [{"seq": None, "id": None, "rule": "L14", "message": "anchoring is not configured"}]
    top = _repo_top(lab)
    remote, branch = cfg.get("remote", "origin"), cfg.get("branch", DEFAULT_BRANCH)
    local_lines = [l for l in lab.log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    try:
        remote_lines = _remote_log(lab, top, remote, branch)
    except LabError as exc:
        return [{"seq": None, "id": None, "rule": "L14", "message": exc.message}]
    if remote_lines is None:
        return [{"seq": None, "id": None, "rule": "L14", "message": f"{remote}/{branch} does not exist"}]
    problem = check_prefix(local_lines, remote_lines)
    errors = [] if problem is None else [{"seq": None, "id": None, "rule": "L14", "message": problem}]
    rel = lab.log_path.resolve().relative_to(top.resolve()).as_posix()
    for a in read_anchors(lab):
        commit = (a.get("receipt") or {}).get("commit")
        if not commit:
            continue
        show = _git(["show", f"{commit}:{rel}"], cwd=top, check=False)
        if show.returncode != 0:
            errors.append({"seq": a.get("seq"), "id": None, "rule": "L14",
                           "message": f"anchor commit {commit[:12]} is not on the remote branch"})
            continue
        anchored = show.stdout.splitlines()
        if len(anchored) != a.get("seq") or check_prefix(local_lines, anchored):
            errors.append({"seq": a.get("seq"), "id": None, "rule": "L14",
                           "message": f"anchor commit {commit[:12]} does not hold the log as it was at seq {a.get('seq')}"})
    return errors
