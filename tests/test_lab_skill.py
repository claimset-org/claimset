"""The claimset-lab skill: its bundled tool runs on its own, and its instructions match the tool."""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from claimset.cli import build_parser
from claimset.lab import guard

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "claimset-lab"
LAUNCHER = SKILL / "scripts" / "claimset"


def lab_subparsers():
    parser = build_parser()
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    lab = top.choices["lab"]
    return next(a for a in lab._actions if isinstance(a, argparse._SubParsersAction)).choices


class TestBundledTool(unittest.TestCase):
    def test_runs_without_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "CLAIMSET_LAB", "CLAIMSET_ACTOR")}

            def run(*args):
                return subprocess.run([sys.executable, "-I", str(LAUNCHER), "lab", *args], cwd=tmp, env=env,
                                      capture_output=True, text=True)

            self.assertEqual(run("init", "--study", "demo", "--title", "T").returncode, 0)
            self.assertTrue((Path(tmp) / "claimset" / "demo" / "lab" / "lab.jsonl").exists())
            self.assertEqual(run("actor", "--kind", "human", "--name", "R").stdout.strip(), "AC1")
            self.assertEqual(run("question", "Q?", "--by", "AC1").stdout.strip(), "Q1")
            out = run("verify")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_guard_config_points_at_the_running_tool(self):
        cmd = guard.guard_command(argv0=str(LAUNCHER), installed="")
        self.assertIn(str(LAUNCHER.resolve()), cmd)
        self.assertTrue(cmd.endswith(" lab guard"))
        self.assertEqual(guard.guard_command(installed="/usr/bin/claimset"), "claimset lab guard")

    def test_guard_allows_bundled_tool_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "lab").mkdir()
            for command in [f'python3 {LAUNCHER} lab note "rm lab/lab.jsonl would be bad" --by AC2',
                            'python3 -m claimset lab note "> lab/lab.jsonl" --by AC2']:
                self.assertIsNone(guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": tmp}),
                                  command)
            blocked = f"python3 {LAUNCHER} lab run --by AC2 -- rm lab/lab.jsonl"
            self.assertIsNotNone(guard.decide({"tool_name": "Bash", "tool_input": {"command": blocked}, "cwd": tmp}))


class TestSkillMatchesTool(unittest.TestCase):
    def test_every_command_and_flag_in_skill_exists(self):
        subs = lab_subparsers()
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        text = text.replace("\\\n", " ")
        uses = re.findall(r"claimset lab ([a-z]+)((?:[^`\n|#]|\|(?=[^`\n]*\"))*)", text)
        self.assertGreater(len(uses), 20)
        for sub, rest in uses:
            self.assertIn(sub, subs, f"`claimset lab {sub}` in SKILL.md is not a command")
            options = {o for a in subs[sub]._actions for o in a.option_strings}
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", rest.split(" -- ")[0]):
                self.assertIn(flag, options, f"`claimset lab {sub} {flag}` in SKILL.md is not an option")

    def test_frontmatter(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"^---\nname: ([a-z-]+)\ndescription: (.+?)\n---\n", text, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "claimset-lab")
        self.assertLess(len(m.group(2)), 1024)


class TestSyncDirectories(unittest.TestCase):
    def test_stale_copy_is_reported(self):
        spec = importlib.util.spec_from_file_location("sync_skills", ROOT / "tools" / "sync_skills.py")
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        extra = SKILL / "scripts" / "lib" / "claimset" / "zz_removed_module.py"
        extra.write_text("")
        try:
            self.assertIn((None, extra), sync.differences())
        finally:
            extra.unlink()
        self.assertEqual(sync.differences(), [])


if __name__ == "__main__":
    unittest.main()
