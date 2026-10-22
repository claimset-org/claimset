"""Where studies, labs and papers live in a repository (lab record spec §3).

    claimset/
      <study>/
        lab/                       the lab record of the study
        code/                      code only this study uses (optional)
        <paper>.claimset.json      papers drawn from the study, with their reading views
        <paper>.claimset.txt

A study is one lab with the questions it records; a repository can hold several. Tools find the lab
of a command from `--lab`, `$CLAIMSET_LAB`, `--study` or `$CLAIMSET_STUDY`, the only study in the
repository, or a legacy `./lab`.
"""

import os
import re
import subprocess
from pathlib import Path

from .core import NAME_RE, LabError

FOLDER = "claimset"
PAPER_SUFFIX = ".claimset.json"
VIEW_SUFFIX = ".claimset.txt"

CODE_FOLDER = "claimset-code"

# Package build files that must leave the studies (and the code only they use) out, and the lines that do it
# (spec §3.3).
IGNORES = [(name, line.format(f=f), f) for f in (FOLDER, CODE_FOLDER) for name, line in (
    (".Rbuildignore", "^{f}$"),
    ("MANIFEST.in", "prune {f}"),
    (".npmignore", "{f}/"),
    (".gitattributes", "{f}/** linguist-documentation"),
)]


def repo_top(start=None):
    """The top of the git repository containing `start` (default: the working directory), or None."""
    start = Path(start or os.getcwd())
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start if start.is_dir() else start.parent,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def base(cwd=None):
    """Where `claimset/` is: the top of the repository, or the working directory outside one."""
    cwd = Path(cwd or os.getcwd())
    return repo_top(cwd) or cwd


def study_dir(study, cwd=None):
    check_name(study, "study")
    return base(cwd) / FOLDER / study


def check_name(name, what="name"):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise LabError("layout", f"'{name}' is not a valid {what} name: use lower-case letters, digits and "
                                 f"hyphens, starting with a letter (e.g. eigen-checker)")
    return name


def studies(cwd=None):
    """{study: lab directory} for every study with a lab record under claimset/."""
    root = base(cwd) / FOLDER
    if not root.is_dir():
        return {}
    return {d.name: d / "lab" for d in sorted(root.iterdir()) if (d / "lab" / "lab.jsonl").is_file()}


def find_lab(lab=None, study=None, cwd=None):
    """The lab directory a command works on."""
    cwd = Path(cwd or os.getcwd())
    explicit = lab or os.environ.get("CLAIMSET_LAB")
    if explicit:
        return Path(explicit)
    study = study or os.environ.get("CLAIMSET_STUDY")
    if study:
        return study_dir(study, cwd) / "lab"
    found = studies(cwd)
    if len(found) == 1:
        return next(iter(found.values()))
    if len(found) > 1:
        raise LabError("layout", f"this repository has several studies ({', '.join(found)}): name one with "
                                 f"--study NAME or $CLAIMSET_STUDY")
    return cwd / "lab"               # a lab from before v0.6, or none yet


def lab_dirs(cwd=None):
    """Every lab directory a command run in `cwd` could write to (for the guard)."""
    cwd = Path(cwd or os.getcwd())
    out = [cwd / (os.environ.get("CLAIMSET_LAB") or "lab")]
    out += list(studies(cwd).values())
    root = base(cwd) / FOLDER
    if root.is_dir():
        out += [d / "lab" for d in root.iterdir() if (d / "lab").is_dir() and d / "lab" not in out]
    return out


def _has(path, line, folder=FOLDER):
    lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    if path.name == ".Rbuildignore":            # R matches each line as a regular expression
        for l in lines:
            try:
                if l and not l.startswith("#") and re.search(l, folder, re.I):
                    return True
            except re.error:
                continue
        return False
    return line in lines


def missing_ignores(top):
    """[(file, line)] for package build files in `top` that do not leave claimset/ out."""
    out = []
    for name, line, folder in IGNORES:
        p = Path(top) / name
        if p.is_file() and not _has(p, line, folder):
            out.append((name, line))
    return out


def add_ignores(top):
    """Add the missing lines to the build files that exist; returns what was added."""
    added = missing_ignores(top)
    for name, line in added:
        p = Path(top) / name
        text = p.read_text(encoding="utf-8")
        p.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n", encoding="utf-8")
    return added


def paper_path(study, name, lab_root=None):
    """claimset/<study>/<name>.claimset.json: beside the lab when it is in its study's folder, else under the
    repository (or working directory)."""
    check_name(name, "paper")
    if lab_root is not None:
        r = Path(lab_root).resolve()
        if r.name == "lab" and r.parent.parent.name == FOLDER and r.parent.name == study:
            return r.parent / f"{name}{PAPER_SUFFIX}"
    return study_dir(study) / f"{name}{PAPER_SUFFIX}"


def studies_top(lab_root):
    """The folder holding claimset/ when the lab is in claimset/<study>/lab, else None."""
    folder = claimset_root(lab_root)
    r = Path(lab_root).resolve()
    if folder is not None and r.name == "lab" and r.parent.parent == folder:
        return folder.parent
    return None


def parse_paper_path(path):
    """(claimset folder, study, paper name) for a path claimset/<study>/<name>.claimset.json, else None."""
    p = Path(path).resolve()
    if not p.name.endswith(PAPER_SUFFIX) or p.parent.parent.name != FOLDER:
        return None
    name = p.name[:-len(PAPER_SUFFIX)]
    return p.parent.parent, p.parent.name, name


def claimset_root(path):
    """The nearest claimset/ folder above a file, or None."""
    for d in Path(path).resolve().parents:
        if d.name == FOLDER:
            return d
    return None
