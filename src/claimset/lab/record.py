"""Study branches (lab record spec v0.7 §3): a study's record lives on its own branch, never merged.

Every study of a repository has a branch `claimset/<study>` that shares no history with the code. It holds
only the study's folder: `lab/`, the papers and their reading views. It is checked out as a git worktree at
`claimset/<study>/` in the code checkout, whose branches ignore `claimset/`. After every `claimset lab`
command that changes the record, the tool commits the study branch and pushes it; the push is the anchor
(§7.1). A commit made after a run has the run's code commit as a further parent, so the code a run used stays
reachable even when its branch is squash-merged or deleted.

The receipt of a push cannot be in the commit it names, and writing it into the checkout after the push would
leave the checkout dirty. It is kept in the checkout's git directory until the next commit, which carries it
into `lab/anchors.jsonl` (spec v0.15 §7.1): the receipt of push n is in commit n+1, and the receipt of the
newest pushed commit, which no commit holds yet, is derived from the remote-tracking branch.
"""

import json
import os
import subprocess
from pathlib import Path

from .core import Lab, LabError, header_hash, NAME_RE

PREFIX = "claimset/"
FOLDER = "claimset"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
IGNORE_LINE = "/claimset/"
ANCHORS = "anchors.jsonl"
PENDING = "claimset-anchors-pending.jsonl"
IDENT = {"GIT_AUTHOR_NAME": "claimset", "GIT_AUTHOR_EMAIL": "claimset@localhost",
         "GIT_COMMITTER_NAME": "claimset", "GIT_COMMITTER_EMAIL": "claimset@localhost"}


def git(args, cwd, check=True, env=None, input=None):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, input=input,
                         env=dict(os.environ, **(env or {})))
    if check and out.returncode != 0:
        raise LabError("record", f"git {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    return out


def _ident():
    """Commits by the tool carry the user's git identity when there is one."""
    env = {}
    for k, v in IDENT.items():
        env[k] = os.environ.get(k) or v
    return env


def top_of(path):
    path = Path(path)
    out = git(["rev-parse", "--show-toplevel"], cwd=path if path.is_dir() else path.parent, check=False)
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else None


def branch_of(top):
    out = git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=top, check=False)
    return out.stdout.strip() if out.returncode == 0 else None


def config(lab):
    """The study-branch configuration of a lab: {remote, branch}, or None."""
    p = Path(lab.root) / "config.json"
    if not p.exists():
        return None
    try:
        cfg = json.loads(p.read_text(encoding="utf-8")).get("record")
    except (ValueError, AttributeError):
        return None
    return cfg if isinstance(cfg, dict) and cfg.get("branch") else None


def _save_record_config(lab, remote, branch):
    p = Path(lab.root) / "config.json"
    cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg.pop("anchor", None)          # the study branch is the anchor from now on; receipts keep the history
    cfg["record"] = {"remote": remote, "branch": branch}
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def anchor_config(lab):
    """The anchoring configuration that study-branch mode implies (every event, on the study branch)."""
    cfg = config(lab)
    if not cfg:
        return None
    return {"service": "git_remote", "remote": cfg.get("remote", "origin"), "branch": cfg["branch"], "every": "event"}


# ---------------------------------------------------------------- the rule that keeps them apart (L20)

def check_writable(lab):
    """Refuse to write a lab that is not on its study branch (rule L20)."""
    root = Path(lab.root).resolve()
    cfg = config(lab)
    top = top_of(root)
    if cfg:
        if top is None or top != root.parent or branch_of(top) != cfg["branch"]:
            raise LabError("L20", f"the lab is written on its study branch {cfg['branch']}, checked out at "
                                  f"claimset/<study>/ (`claimset setup` attaches it); this copy is not that "
                                  f"checkout")
        return
    if top is not None and root.name == "lab" and root.parent.parent.name == FOLDER and top != root.parent:
        study = root.parent.name
        raise LabError("L20", f"the lab of study '{study}' sits in the code checkout; study records live on "
                              f"their own branch: `claimset setup --study {study} --adopt --by <you>` moves it there")


# ---------------------------------------------------------------- committing and pushing

def _pending_path(lab):
    """Where the receipts of pushes not yet committed are kept: the study checkout's git directory, which no
    commit and no `git status` sees."""
    top = Path(lab.root).resolve().parent
    out = git(["rev-parse", "--absolute-git-dir"], cwd=top, check=False)
    return Path(out.stdout.strip()) / PENDING if out.returncode == 0 and out.stdout.strip() else None


def _jsonl(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return [l for l in text.splitlines() if l.strip()]


def pending(lab):
    """Receipts of pushes that no commit holds yet, as JSON lines."""
    if not config(lab):
        return []
    p = _pending_path(lab)
    return _jsonl(p) if p else []


def _fold_pending(lab, top):
    """Append to lab/anchors.jsonl the receipts no commit holds yet: the pending ones, and the derived receipt of
    the newest pushed commit when this checkout did not push it (a clone). Returns True if any was added."""
    path = Path(lab.root) / ANCHORS
    have = _jsonl(path)
    lines = pending(lab)
    known = []
    for l in have + lines:
        try:
            known.append(json.loads(l))
        except ValueError:
            pass
    d = derived_receipt(lab, known)
    if d:
        lines.append(json.dumps(d, separators=(",", ":")))
    new = [l for l in lines if l not in set(have)]
    if new:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("".join(l + "\n" for l in new))
        git(["add", "--", str(path)], cwd=top)
    return bool(new)


def commit(lab, message, extra_parents=()):
    """Commit the study folder to its branch, with the receipts of earlier pushes (§7.1). Returns the commit,
    or None if nothing changed: receipts alone are not a change, they wait for the next commit."""
    top = Path(lab.root).resolve().parent
    git(["add", "-A", "."], cwd=top)
    head = git(["rev-parse", "--verify", "-q", "HEAD"], cwd=top, check=False).stdout.strip()
    if head and not git(["diff", "--cached", "--quiet", "HEAD"], cwd=top, check=False).returncode:
        return None
    _fold_pending(lab, top)
    tree = git(["write-tree"], cwd=top).stdout.strip()
    args = ["commit-tree", tree, "-m", message]
    parents = [head] if head else []
    for p in extra_parents:
        if p and p not in parents and git(["cat-file", "-e", f"{p}^{{commit}}"], cwd=top, check=False).returncode == 0:
            parents.append(p)
    for p in parents:
        args += ["-p", p]
    c = git(args, cwd=top, env=_ident()).stdout.strip()
    git(["update-ref", "HEAD", c], cwd=top)
    p = _pending_path(lab)
    if p and p.exists():
        p.unlink()                      # the receipts are in the commit now
    return c


def push(lab):
    """Push the study branch; keep the anchor entry until the next commit (§7.1). Raises LabError if the push
    fails."""
    cfg = config(lab)
    top = Path(lab.root).resolve().parent
    remote, branch = cfg.get("remote", "origin"), cfg["branch"]
    out = git(["push", "-q", remote, f"HEAD:refs/heads/{branch}"], cwd=top, check=False)
    if out.returncode != 0:
        raise LabError("anchor", f"push of {branch} to {remote} failed: {out.stderr.strip()}")
    events = lab.read()
    commit_id = git(["rev-parse", "HEAD"], cwd=top).stdout.strip()
    entry = {"seq": events[-1]["seq"], "head_hash": header_hash(events[-1]), "service": "git_remote",
             "receipt": {"remote": remote, "branch": branch, "commit": commit_id}, "at": lab.clock()}
    agents = {}
    for actor in lab.agent_logs():
        a = lab.read_agent(actor)
        if a:
            agents[actor] = {"seq": a[-1]["seq"], "head_hash": header_hash(a[-1])}
    if agents:
        entry["agents"] = agents
    known = [json.loads(l) for l in _jsonl(Path(lab.root) / ANCHORS) + pending(lab)]
    if any(isinstance(a, dict) and (a.get("receipt") or {}).get("commit") == commit_id for a in known):
        return entry                    # this commit was pushed and received before
    p = _pending_path(lab)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return entry


def derived_receipt(lab, known):
    """The receipt of the newest pushed commit when no commit or pending file holds it (a fresh clone, another
    checkout): derived from the remote-tracking branch, which names a commit the remote has, and from the logs
    in that commit. Its `at` is the commit's date. None when there is nothing to derive."""
    cfg = config(lab)
    if not cfg:
        return None
    top = Path(lab.root).resolve().parent
    if top_of(top) != top:
        return None
    remote, branch = cfg.get("remote", "origin"), cfg["branch"]
    ref = git(["rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}"], cwd=top, check=False).stdout.strip()
    if not ref or any((a.get("receipt") or {}).get("commit") == ref for a in known if isinstance(a, dict)):
        return None
    if git(["merge-base", "--is-ancestor", ref, "HEAD"], cwd=top, check=False).returncode != 0:
        return None

    def last(rel):
        out = git(["show", f"{ref}:{rel}"], cwd=top, check=False)
        lines = [l for l in out.stdout.splitlines() if l.strip()] if out.returncode == 0 else []
        try:
            return json.loads(lines[-1]) if lines else None
        except ValueError:
            return None
    lab_rel = Path(lab.root).resolve().relative_to(top).as_posix()
    head = last(f"{lab_rel}/lab.jsonl")
    if not isinstance(head, dict) or not isinstance(head.get("seq"), int):
        return None
    at = git(["show", "-s", "--format=%cd", "--date=format-local:%Y-%m-%dT%H:%M:%SZ", ref], cwd=top,
             env={"TZ": "UTC"}).stdout.strip()
    entry = {"seq": head["seq"], "head_hash": header_hash(head), "service": "git_remote",
             "receipt": {"remote": remote, "branch": branch, "commit": ref}, "at": at, "derived": True}
    names = git(["ls-tree", "--name-only", f"{ref}:{lab_rel}/agents"], cwd=top, check=False)
    agents = {}
    for name in (names.stdout.split() if names.returncode == 0 else []):
        if name.endswith(".jsonl"):
            e = last(f"{lab_rel}/agents/{name}")
            if isinstance(e, dict) and isinstance(e.get("seq"), int):
                agents[name[:-len(".jsonl")]] = {"seq": e["seq"], "head_hash": header_hash(e)}
    if agents:
        entry["agents"] = agents
    return entry


def migrate(lab):
    """Records kept by tools before lab record v0.15 left the receipt of the last push in lab/anchors.jsonl,
    uncommitted. Commit it, so that the checkout is clean; the push of that commit is received as usual.
    Returns the commit, or None if there was nothing to migrate."""
    if not config(lab):
        return None
    top = Path(lab.root).resolve().parent
    rel = (Path(lab.root) / ANCHORS).resolve().relative_to(top).as_posix()
    if not git(["status", "--porcelain", "--", rel], cwd=top).stdout.strip():
        return None
    git(["add", "--", rel], cwd=top)
    head = git(["rev-parse", "--verify", "-q", "HEAD"], cwd=top, check=False).stdout.strip()
    tree = git(["write-tree"], cwd=top).stdout.strip()
    c = git(["commit-tree", tree, "-m", "claimset lab: commit anchor receipts (lab record v0.15)"]
            + (["-p", head] if head else []), cwd=top, env=_ident()).stdout.strip()
    git(["update-ref", "HEAD", c], cwd=top)
    return c


def save(lab, before=0, message=None):
    """After a command: commit what changed (the new events, run outputs, papers) and push. The commit after
    a run takes the run's code commit as a further parent. Returns the anchor entry, or None."""
    if not config(lab):
        return None
    migrated = migrate(lab)
    events = lab.read()
    new = events[before:]
    codes = [e["body"].get("code_version") for e in new if e.get("type") == "run" and isinstance(e.get("body"), dict)]
    if new:
        ids = ", ".join(e["id"] for e in new[:5]) + (" …" if len(new) > 5 else "")
        message = message or f"claimset lab: seq {new[0]['seq']}-{new[-1]['seq']} ({ids})"
    c = commit(lab, message or "claimset lab: update", codes)
    if c is None and not new and not migrated:
        return None
    try:
        return push(lab)
    except LabError as exc:
        print(f"claimset: WARNING the record is committed on its branch but not pushed: {exc.message}. "
              f"It is pushed with the next command; `claimset lab anchor` retries now.", file=__import__("sys").stderr)
        return None


def unpushed(lab):
    """Commits of the study branch not on the remote (None if unknown)."""
    cfg = config(lab)
    if not cfg:
        return None
    top = Path(lab.root).resolve().parent
    remote, branch = cfg.get("remote", "origin"), cfg["branch"]
    ref = git(["rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}"], cwd=top, check=False).stdout.strip()
    if not ref:
        return None
    out = git(["rev-list", "--count", f"{ref}..HEAD"], cwd=top, check=False)
    return int(out.stdout.strip()) if out.returncode == 0 else None


# ---------------------------------------------------------------- setting up

def _ensure_line(path, line):
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if line in [l.strip() for l in text.splitlines()]:
        return False
    path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n", encoding="utf-8")
    return True


def code_top(cwd=None):
    top = top_of(Path(cwd or os.getcwd()))
    if top is None:
        raise LabError("record", "not inside a git repository: study branches need one")
    b = branch_of(top)
    if b and b.startswith(PREFIX):
        raise LabError("record", f"{top} is the checkout of the study branch {b}; run this from the code checkout")
    return top


def _remote_has(top, remote, branch):
    out = git(["ls-remote", "--heads", remote, branch], cwd=top, check=False)
    return out.returncode == 0 and bool(out.stdout.strip())


def setup(study, title=None, repository=None, remote="origin", adopt=False, by=None, cwd=None, clock=None):
    """Create, attach or adopt the study branch of `study`; returns (Lab, what was done, notes)."""
    if not NAME_RE.match(study or ""):
        raise LabError("layout", f"'{study}' is not a valid study name: lower-case letters, digits and hyphens, "
                                 f"starting with a letter")
    top = code_top(cwd)
    branch, path = f"{PREFIX}{study}", top / FOLDER / study
    notes = []
    if _ensure_line(top / ".gitignore", IGNORE_LINE):
        notes.append(f"added '{IGNORE_LINE}' to .gitignore: the code branches never hold study records")
    from .layout import add_ignores
    for name, line in add_ignores(top):
        notes.append(f"added '{line}' to {name}, so package builds leave the studies out")

    if path.exists() and top_of(path) == path.resolve() and branch_of(path) == branch:
        lab = Lab(path / "lab")
        if not config(lab):
            _save_record_config(lab, remote, branch)
        return lab, "attached already", notes

    tracked = git(["ls-files", "--", f"{FOLDER}/{study}"], cwd=top).stdout.strip()
    if tracked:
        if not adopt:
            raise LabError("record", f"study '{study}' is on the code branch ({FOLDER}/{study}/); move it to its own "
                                     f"branch with `claimset setup --study {study} --adopt --by <you>`")
        return _adopt(top, study, branch, path, remote, by, notes, clock, title, repository)
    if path.exists() and any(path.iterdir()):
        raise LabError("record", f"{path} exists and is not the checkout of {branch}")

    local = git(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], cwd=top, check=False).returncode == 0
    if not local and _remote_has(top, remote, branch):
        git(["fetch", "-q", remote, f"refs/heads/{branch}:refs/heads/{branch}"], cwd=top)
        local = True
    if local:
        git(["worktree", "add", "-q", str(path), branch], cwd=top)
        git(["branch", "-q", f"--set-upstream-to={remote}/{branch}", branch], cwd=top, check=False)
        return Lab(path / "lab"), "attached", notes

    if not title:
        raise LabError("record", f"study '{study}' is new: give its working title with --title")
    root = git(["commit-tree", EMPTY_TREE, "-m", f"claimset: study {study}"], cwd=top, env=_ident()).stdout.strip()
    git(["branch", branch, root], cwd=top)
    git(["worktree", "add", "-q", str(path), branch], cwd=top)
    lab = Lab.create(path / "lab", title, repository, study=study, **({"clock": clock} if clock else {}))
    _save_record_config(lab, remote, branch)
    save(lab, 0, f"claimset: study {study}")
    return lab, "created", notes


def _adopt(top, study, branch, path, remote, by, notes, clock, title=None, repository=None):
    """Move a study folder committed on the code branch onto its own branch (§3.4). A folder without a lab
    (a paper written before any record) gets a new lab, begun at the adoption."""
    has_lab = (path / "lab" / "lab.jsonl").exists()
    if has_lab and not by:
        raise LabError("L8", "say who records the move: --by ACTOR")
    if not has_lab and not title:
        raise LabError("record", f"{FOLDER}/{study}/ has no lab: give the working title of the record begun now "
                                 f"with --title")
    rel = f"{FOLDER}/{study}"
    if git(["status", "--porcelain", "--", rel], cwd=top).stdout.strip():
        raise LabError("record", f"{rel}/ has uncommitted changes on the code branch; commit them first")
    loose = [f for f in git(["ls-files", "--others", "--ignored", "--exclude-standard", "--", rel], cwd=top).stdout.split()
             if not f.endswith("/.lock")] + \
        [f for f in git(["ls-files", "--others", "--", rel], cwd=top).stdout.split() if not f.endswith("/.lock")]
    if loose:
        raise LabError("record", f"{rel}/ holds files that are not committed ({', '.join(sorted(set(loose))[:5])}); "
                                 f"commit or remove them first")
    code_branch = branch_of(top) or "HEAD"
    head = git(["rev-parse", "HEAD"], cwd=top).stdout.strip()
    tree = git(["rev-parse", f"HEAD:{rel}"], cwd=top).stdout.strip()
    body = {"from": f"{rel}/lab", "to": "lab", "branch": branch}
    if has_lab:
        st = Lab(path / "lab").state()
        st.check("relocation", st.next_id("relocation"), by, body, None)     # refuse before changing anything
    # code/ stays with the code: on the code branch, in claimset-code/<study>/ (§3)
    entries = git(["ls-tree", tree], cwd=top).stdout.splitlines()
    has_code = any(e.split("\t", 1)[1] == "code" for e in entries if "\t" in e)
    if has_code:
        tree = git(["mktree"], cwd=top, input="\n".join(e for e in entries if e.split("\t", 1)[1] != "code") + "\n"
                   ).stdout.strip()
    root = git(["commit-tree", tree, "-m", f"claimset: study {study}, adopted from {code_branch} at {head[:12]}"],
               cwd=top, env=_ident()).stdout.strip()
    # the code it came with stays reachable from the record: a second parent
    pinned = git(["commit-tree", tree, "-p", root, "-p", head, "-m", f"claimset: code of {study} up to {head[:12]}"],
                 cwd=top, env=_ident()).stdout.strip()
    git(["branch", branch, pinned], cwd=top)
    if has_code:
        from .layout import CODE_FOLDER
        dest = top / CODE_FOLDER / study
        if dest.exists():
            raise LabError("record", f"{CODE_FOLDER}/{study} exists; move {rel}/code yourself, then adopt again")
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(["mv", f"{rel}/code", f"{CODE_FOLDER}/{study}"], cwd=top)
        body["moved"] = [{"from": f"{rel}/code", "to": f"{CODE_FOLDER}/{study}"}]
        notes.append(f"moved {rel}/code to {CODE_FOLDER}/{study} on {code_branch} (staged)")
    git(["rm", "-r", "-q", rel], cwd=top)
    if path.exists():                          # empty folders git does not track (runs/, agents/), the lock
        import shutil
        shutil.rmtree(path)
    git(["worktree", "add", "-q", str(path), branch], cwd=top)
    if has_lab:
        lab = Lab(path / "lab", **({"clock": clock} if clock else {}))
        lab.append("relocation", body, by)
    else:
        lab = Lab.create(path / "lab", title, repository, study=study, **({"clock": clock} if clock else {}))
        notes.append(f"began a lab record for {study}: what was done before it is not recorded")
    _save_record_config(lab, remote, branch)
    save(lab, len(lab.read()) - 1, f"claimset: {study} moved to its own branch")
    notes.append(f"removed {rel}/ from {code_branch} (staged): commit that with the .gitignore change")
    return lab, "adopted", notes


def attach_all(remote="origin", cwd=None):
    """Attach every study branch of the remote that is not checked out yet (a fresh clone)."""
    top = code_top(cwd)
    out = git(["ls-remote", "--heads", remote, f"{PREFIX}*"], cwd=top, check=False).stdout.split()
    names = [r[len("refs/heads/" + PREFIX):] for r in out if r.startswith("refs/heads/" + PREFIX)]
    done = []
    for study in names:
        if NAME_RE.match(study):
            lab, what, _ = setup(study, remote=remote, cwd=top)
            done.append((study, what))
    return done


def studies(cwd=None):
    """[{study, branch, path, attached, events, open, pending, unpushed}] for the studies of this repository."""
    from .summary import build
    top = top_of(Path(cwd or os.getcwd()))
    if top is None:
        return []
    b = branch_of(top)
    if b and b.startswith(PREFIX):
        top = top.parent.parent if top.parent.name == FOLDER else top
    branches = git(["for-each-ref", "--format=%(refname:short)", f"refs/heads/{PREFIX}*",
                    f"refs/remotes/*/{PREFIX}*"], cwd=top, check=False).stdout.split()
    names = sorted({x.split(PREFIX, 1)[1] for x in branches if PREFIX in x})
    folder = top / FOLDER
    if folder.is_dir():
        names = sorted(set(names) | {d.name for d in folder.iterdir() if (d / "lab" / "lab.jsonl").exists()})
    out = []
    for name in names:
        path = folder / name
        lab = Lab(path / "lab")
        info = {"study": name, "branch": f"{PREFIX}{name}", "path": str(path), "attached": lab.exists()}
        if lab.exists():
            try:
                s = build(lab.read())
                info.update(events=s["events"], open=len(s["open_hypotheses"]), pending=len(s["pending_plans"]),
                            unpushed=unpushed(lab), on_branch=bool(config(lab)))
            except LabError:
                info["unreadable"] = True
        out.append(info)
    return out


CI_WORKFLOW = """name: claimset study branches

# Study records live on claimset/<study> branches and are never merged (lab record spec v0.7, section 3).
on: pull_request

jobs:
  separation:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Study records stay on their branches
        env:
          HEAD_REF: ${{ github.head_ref }}
          BASE_REF: ${{ github.base_ref }}
        run: |
          case "$HEAD_REF" in claimset/*) echo "::error::$HEAD_REF holds a study record; study branches are never merged"; exit 1;; esac
          case "$BASE_REF" in claimset/*) echo "::error::nothing is merged into the study branch $BASE_REF"; exit 1;; esac
          git fetch -q origin '+refs/heads/claimset/*:refs/remotes/origin/claimset/*' || true
          if git diff --name-only "origin/$BASE_REF"...HEAD | grep -q '^claimset/'; then
            echo "::error::this pull request changes claimset/, which lives on the study branches"; exit 1
          fi
          for b in $(git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/claimset/*'); do
            for root in $(git rev-list --first-parent --max-parents=0 "$b"); do
              if git merge-base --is-ancestor "$root" HEAD; then
                echo "::error::this pull request contains the history of the study branch $b"; exit 1
              fi
            done
          done
"""


def write_ci(cwd=None):
    top = code_top(cwd)
    p = top / ".github" / "workflows" / "claimset-study-branches.yml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CI_WORKFLOW, encoding="utf-8")
    return p
