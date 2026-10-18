#!/usr/bin/env python3
"""Copy the canonical spec, package and examples into the bundled skills.

Skills must be self-contained when installed, so they carry copies of files
whose source of truth lives elsewhere in this repository. Run this script after
changing any of the sources; tests/test_sync.py fails if the copies drift.

    python3 tools/sync_skills.py          # copy
    python3 tools/sync_skills.py --check  # report differences, exit 1 if any
"""
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec"

# (source, destination) pairs, relative to the repository root.
COPIES = {
    "claimset-submit": [
        ("src/claimset/validate.py", "skills/claimset-submit/scripts/validate.py"),
        ("spec/claimset-v0.18.md", "skills/claimset-submit/references/spec-v0.18.md"),
        ("spec/lab-record-v0.5.md", "skills/claimset-submit/references/lab-record-v0.5.md"),
        ("examples/submission-native.json", "skills/claimset-submit/examples/submission-native.json"),
        ("examples/published-native.json", "skills/claimset-submit/examples/published-native.json"),
        ("examples/submission-from-lab.json", "skills/claimset-submit/examples/submission-from-lab.json"),
    ],
    "claimset-lab": [
        ("src/claimset", "skills/claimset-lab/scripts/lib/claimset"),   # the whole package
        ("spec/lab-record-v0.5.md", "skills/claimset-lab/references/lab-record-v0.5.md"),
    ],
}


def _sources(src):
    """Files under a source path: the file itself, or every .py file of a package directory."""
    if src.is_dir():
        return [f for f in sorted(src.rglob("*.py")) if "__pycache__" not in f.parts]
    return [src]


def pairs():
    for items in COPIES.values():
        for src, dst in items:
            src, dst = ROOT / src, ROOT / dst
            for f in _sources(src):
                yield f, (dst / f.relative_to(src)) if src.is_dir() else dst


def stale():
    """Files in a copied package directory that no longer have a source."""
    expected = {dst for _, dst in pairs()}
    out = []
    for items in COPIES.values():
        for src, dst in items:
            if (ROOT / src).is_dir() and (ROOT / dst).is_dir():
                out += [f for f in sorted((ROOT / dst).rglob("*.py"))
                        if "__pycache__" not in f.parts and f not in expected]
    return out


def differences():
    out = []
    for src, dst in pairs():
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            out.append((src, dst))
    return out + [(None, f) for f in stale()]


def main(argv):
    if "--check" in argv:
        diff = differences()
        for src, dst in diff:
            source = f"source {src.relative_to(ROOT)}" if src else "no source: remove it"
            print(f"out of date: {dst.relative_to(ROOT)} ({source})")
        return 1 if diff else 0
    for src, dst in pairs():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"copied {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")
    for f in stale():
        f.unlink()
        print(f"removed {f.relative_to(ROOT)} (its source is gone)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
