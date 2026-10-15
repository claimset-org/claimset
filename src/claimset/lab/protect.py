"""Operating-system append-only attribute on the log files (lab record spec §4.5).

On Linux, `chattr +a` makes the kernel refuse any change to a file except
appending: no edits, truncation, renaming or deletion, even by its owner. Only
an administrator can set or remove the attribute, so it protects the log from
any agent that cannot act as an administrator.
"""

import platform
import shutil
import subprocess

from .anchor import ANCHORS_FILE


def protect(lab, undo=False, chattr="chattr"):
    """Set (or remove) the append-only attribute. Returns {"ok", "messages"}."""
    files = [lab.log_path, lab.root / ANCHORS_FILE]
    for f in files:
        if not f.exists():
            f.touch()
    agents = lab.root / "agents"
    if agents.is_dir():
        files += sorted(agents.glob("*.jsonl"))
    flag = "-a" if undo else "+a"
    paths = " ".join(str(f) for f in files)
    if platform.system() != "Linux" or shutil.which(chattr) is None:
        return {"ok": False, "messages": [
            "The append-only attribute is set with chattr on Linux, which is not available here.",
            "On macOS an administrator can run: sudo chflags sappnd " + paths,
            "(the system flag needs an administrator to remove, unlike the user flag uappnd)."]}
    out = subprocess.run([chattr, flag] + [str(f) for f in files], capture_output=True, text=True)
    if out.returncode != 0:
        return {"ok": False, "messages": [
            f"Could not run chattr {flag}: {out.stderr.strip() or 'permission denied'}.",
            "Setting the attribute needs an administrator, which is what makes it protective. Run:",
            f"  sudo chattr {flag} {paths}",
            "Note: some file systems (e.g. tmpfs, many network and overlay mounts) do not support it."]}
    state = "removed from" if undo else "set on"
    return {"ok": True, "messages": [f"Append-only attribute {state}: {paths}",
                                     "Only an administrator can remove it (sudo chattr -a ...)."]}
