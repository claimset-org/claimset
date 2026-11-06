#!/usr/bin/env python3
"""Freeze the current validator before the specification moves to a new version (claimset spec §2.6).

Run this BEFORE changing SCHEMA_VERSION in src/claimset/validate.py:

    python3 tools/freeze_validator.py

It copies src/claimset/validate.py to src/claimset/versions/v<major>_<minor>.py for the current SCHEMA_VERSION,
and adds the version to FROZEN in src/claimset/versions/__init__.py. Files declaring that version are then
validated by the frozen copy, by every later validator; tests/compat/<version>/ keeps checking that they are.
Then raise SCHEMA_VERSION, change the rules, and add files of the new version to tests/compat/<new version>/.

Until the baseline is set (BASELINE_VERSION in validate.py, set when the platform launches) no version is kept:
the tool refuses, and SCHEMA_VERSION is raised directly.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "claimset"


def main():
    text = (SRC / "validate.py").read_text(encoding="utf-8")
    version = re.search(r'^SCHEMA_VERSION = "([0-9.]+)"$', text, re.M).group(1)
    if re.search(r"^BASELINE_VERSION = None", text, re.M):
        sys.exit("The baseline is not set (BASELINE_VERSION = None): earlier versions are not kept yet. Raise "
                 "SCHEMA_VERSION directly, and replace tests/compat/ with files of the new version.")
    major, minor = version.split(".")
    target = SRC / "versions" / f"v{major}_{minor}.py"
    if target.exists():
        sys.exit(f"{target.relative_to(ROOT)} exists: version {version} is frozen already")
    # The copy lives one package level down (claimset.versions): its imports from the package go one level up.
    text = re.sub(r"^(\s*)from \.(\w)", r"\1from ..\2", text, flags=re.M)
    text = re.sub(r"^(\s*)from \. import", r"\1from .. import", text, flags=re.M)
    header = (f'"""Frozen validator of claimset specification v{version}. Its rules never change: it defines that '
              f'version\'s rules (spec §2.6). Bugs are fixed here too."""\n\n')
    target.write_text(header + text, encoding="utf-8")
    init = SRC / "versions" / "__init__.py"
    t = init.read_text(encoding="utf-8")
    m = re.search(r"^FROZEN = \((.*)\)$", t, re.M)
    versions = [v.strip().strip('"') for v in m.group(1).split(",") if v.strip()]
    versions.append(version)
    t = t[:m.start()] + "FROZEN = (" + ", ".join(f'"{v}"' for v in versions) + ",)" + t[m.end():]
    init.write_text(t, encoding="utf-8")
    print(f"froze v{version} as {target.relative_to(ROOT)}; now raise SCHEMA_VERSION in validate.py")


if __name__ == "__main__":
    main()
