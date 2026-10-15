"""Guard for agent harnesses (lab record spec §4.5).

Installed as a pre-tool-use hook, it receives each proposed tool call as JSON on
standard input and refuses (exit code 2, reason on standard error) any call that
would modify the lab directory other than through `claimset lab`. It is a
guardrail against accidental or casual modification, not a security boundary:
the append-only attribute and anchoring cover deliberate tampering.

Input fields used: `tool_name`, `tool_input`, `cwd` (Claude Code's hook input).
"""

import json
import os
import re
import shlex
from pathlib import Path

from .anchor import DEFAULT_BRANCH

FILE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}

# Commands and constructs that change files. Reading (cat, grep, jq, ls, git log/diff) is allowed.
# The match is deliberately broad: when in doubt the guard refuses and points to `claimset lab`.
DESTRUCTIVE = re.compile(
    r"(?:^|[\s;&|(`])(?:rm|rmdir|mv|cp|install|truncate|shred|unlink|chattr|dd|tee|touch|ln|rsync|chmod|chown)\b"
    r"|\b(?:sed|perl|ruby)\s+(?:-\w*\s+)*-i"
    r"|\bgit\s+(?:checkout|restore|reset|clean|rm|mv|stash|filter-branch|filter-repo|rebase|update-ref)\b"
    r"|\bgit\s+commit\b[^;&|]*--amend"
    r"|\bopen\([^)]*['\"][wa+]"
    r"|\b(?:write_text|write_bytes|unlink|rmtree|remove|rename|replace)\("
)
FORCE_PUSH = re.compile(r"\bgit\s+push\b[^;&|]*(?:\s-f\b|--force|\s\+\S+)")
DELETE_BRANCH = re.compile(r"\bgit\s+push\b[^;&|]*(?:--delete|\s:\S+)")
MIRROR = re.compile(r"\bgit\s+push\b[^;&|]*--mirror")
ALL = re.compile(r"\bgit\s+push\b[^;&|]*--all")
REDIRECT = re.compile(r"(?:\d|&)?>{1,2}\|?\s*(['\"]?)([^\s'\";|&)]+)\1")
OPERATOR = re.compile(r"^[;&|<>()]+$")


def lab_dir(cwd):
    """The lab directory the agent would use: $CLAIMSET_LAB or ./lab, relative to cwd."""
    return Path(cwd) / (os.environ.get("CLAIMSET_LAB") or "lab")


def _lab_subcommand(command):
    """For a plain `claimset lab <sub> ...` command, (sub, remaining tokens); else None.

    A command with shell operators (;, &&, |, >, ...) outside quotes is not plain.
    """
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return None
    if any(OPERATOR.match(t) for t in tokens):
        return None
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    if len(tokens) < 2 or Path(tokens[0]).name != "claimset" or tokens[1] != "lab":
        return None
    return (tokens[2] if len(tokens) > 2 else ""), tokens[3:]


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _mentions(command, root, cwd):
    """True if the command refers to the lab directory or a file in it."""
    root = Path(root)
    names = {str(root.resolve())}
    try:
        names.add(os.path.relpath(root.resolve(), Path(cwd).resolve()))
    except ValueError:
        pass
    for name in names:
        n = name.rstrip("/")
        if re.search(r"(?:^|[\s'\"=:(/])" + re.escape(n) + r"(?:/|[\s'\";|&)]|$)", command):
            return True
    return False


def _anchor_branch(root):
    try:
        cfg = json.loads((Path(root) / "config.json").read_text(encoding="utf-8"))
        return (cfg.get("anchor") or {}).get("branch") or DEFAULT_BRANCH
    except (OSError, ValueError, AttributeError):
        return DEFAULT_BRANCH


def decide(payload, branch=None):
    """Return None to allow the tool call, or the reason for refusing it."""
    tool = payload.get("tool_name")
    inp = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    root = lab_dir(cwd)
    branch = branch or _anchor_branch(root)

    if tool in FILE_TOOLS:
        path = inp.get(FILE_TOOLS[tool])
        if path and _inside(Path(cwd) / path, root):
            return (f"{path} is part of the lab record, which is append-only. Record changes with "
                    f"`claimset lab` commands (corrections with --supersedes), never by editing files.")
        return None

    if tool == "Bash":
        command = inp.get("command") or ""
        plain = _lab_subcommand(command)
        if plain is not None:
            sub, rest = plain
            if sub != "run":
                return None
            # The command a run wraps is checked like any other command.
            command = " ".join(shlex.quote(t) for t in rest[rest.index("--") + 1:]) if "--" in rest else ""
        rewrites = FORCE_PUSH.search(command) or DELETE_BRANCH.search(command) or MIRROR.search(command)
        if rewrites and (branch in command or MIRROR.search(command) or ALL.search(command)):
            return f"force-pushing or deleting the anchor branch '{branch}' would rewrite the anchored lab record."
        for _, target in REDIRECT.findall(command):
            if _inside(Path(cwd) / os.path.expanduser(target), root):
                return (f"writing to {target} with a shell redirection would modify the lab record, which is "
                        f"append-only. Use `claimset lab` commands to add to it.")
        if _mentions(command, root, cwd) and DESTRUCTIVE.search(command):
            return ("this command would modify the lab record, which is append-only. Use `claimset lab` "
                    "commands to add to it; read it with `claimset lab list`, `show` or `summary`.")
        return None
    return None


def main_hook(stdin, stderr):
    try:
        payload = json.load(stdin)
    except json.JSONDecodeError:
        return 0
    reason = decide(payload)
    if reason:
        stderr.write(f"claimset guard: blocked. {reason}\n")
        return 2
    return 0


def claude_code_config():
    """Settings snippet installing the guard as a Claude Code PreToolUse hook."""
    return {"hooks": {"PreToolUse": [{
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
        "hooks": [{"type": "command", "command": "claimset lab guard"}],
    }]}}

