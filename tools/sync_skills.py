#!/usr/bin/env python3
"""Copy the canonical spec, validator and examples into the bundled skills.

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
        ("spec/claimset-v0.11.md", "skills/claimset-submit/references/spec-v0.11.md"),
        ("spec/lab-record-v0.4.md", "skills/claimset-submit/references/lab-record-v0.4.md"),
        ("examples/submission-native.json", "skills/claimset-submit/examples/submission-native.json"),
        ("examples/published-native.json", "skills/claimset-submit/examples/published-native.json"),
        ("examples/submission-from-lab.json", "skills/claimset-submit/examples/submission-from-lab.json"),
    ],
}


def pairs():
    for items in COPIES.values():
        for src, dst in items:
            yield ROOT / src, ROOT / dst


def differences():
    out = []
    for src, dst in pairs():
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            out.append((src, dst))
    return out


def main(argv):
    if "--check" in argv:
        diff = differences()
        for src, dst in diff:
            print(f"out of date: {dst.relative_to(ROOT)} (source {src.relative_to(ROOT)})")
        return 1 if diff else 0
    for src, dst in pairs():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"copied {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
