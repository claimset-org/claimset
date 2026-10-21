"""The package carries its specifications and installs skills that match it."""

import filecmp
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claimset import SPEC_VERSION, bundle, cli  # noqa: E402
from claimset.validate import validate  # noqa: E402


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


class TestSpec(unittest.TestCase):
    def test_spec_command_prints_the_spec_the_validator_uses(self):
        code, out = run(["spec"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(f"# Claimset — Specification v{SPEC_VERSION}"))
        code, out = run(["spec", "--lab"])
        self.assertTrue(out.startswith("# Claimset Lab Record"), out[:60])

    def test_older_schema_is_explained(self):
        d = json.loads((ROOT / "examples" / "submission-native.json").read_text(encoding="utf-8"))
        d["schema_version"] = "0.9"
        msg = next(e["message"] for e in validate(d).errors if e["rule"] == "2")
        self.assertIn("follows specification 0.9", msg)
        self.assertIn("claimset skill install", msg)


class TestSkills(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def test_installed_skills_equal_the_repository_skills(self):
        code, out = run(["skill", "install", "--dir", str(self.dir)])
        self.assertEqual(code, 0, out)
        for name in bundle.SKILLS:
            repo = ROOT / "skills" / name
            mine = {f.relative_to(repo).as_posix() for f in repo.rglob("*")
                    if f.is_file() and "__pycache__" not in f.parts}
            got = {f.relative_to(self.dir / name).as_posix() for f in (self.dir / name).rglob("*") if f.is_file()}
            self.assertEqual(got, mine, name)
            for rel in mine:
                self.assertTrue(filecmp.cmp(repo / rel, self.dir / name / rel, shallow=False), rel)
        self.assertEqual(run(["skill", "check", "--dir", str(self.dir)])[0], 0)
        self.assertEqual(bundle.install(self.dir, "claimset-submit"), "current")

    def test_an_old_copy_is_found_and_replaced(self):
        old = self.dir / "claimset-submit"
        (old / "references").mkdir(parents=True)
        (old / "examples").mkdir()
        (old / "SKILL.md").write_text("---\nname: claimset-submit\ndescription: v0.9\n---\n", encoding="utf-8")
        (old / "references" / "spec-v0.9.md").write_text("old", encoding="utf-8")
        (old / "examples" / "minimal-native.json").write_text("{}", encoding="utf-8")
        code, out = run(["skill", "check", "claimset-submit", "--dir", str(self.dir)])
        self.assertEqual(code, 1)
        self.assertIn("references/spec-v0.9.md: not part of this version", out)
        self.assertIn("SKILL.md: differs", out)
        self.assertEqual(bundle.install(self.dir, "claimset-submit"), "replaced")
        self.assertFalse((old / "references" / "spec-v0.9.md").exists())
        self.assertFalse((old / "examples" / "minimal-native.json").exists())
        self.assertTrue((old / "references" / f"spec-v{SPEC_VERSION}.md").exists())
        self.assertEqual(bundle.check(self.dir, "claimset-submit"), [])

    def test_a_foreign_directory_is_not_replaced_without_force(self):
        other = self.dir / "claimset-lab"
        other.mkdir()
        (other / "notes.txt").write_text("mine", encoding="utf-8")
        code, _ = run(["skill", "install", "claimset-lab", "--dir", str(self.dir)])
        self.assertEqual(code, 1)
        self.assertTrue((other / "notes.txt").exists())
        self.assertEqual(run(["skill", "install", "claimset-lab", "--dir", str(self.dir), "--force"])[0], 0)
        self.assertFalse((other / "notes.txt").exists())

    def test_installed_skills_work_without_the_package(self):
        bundle.install(self.dir, "claimset-lab")
        bundle.install(self.dir, "claimset-submit")
        lab = subprocess.run([sys.executable, "-I", str(self.dir / "claimset-lab" / "scripts" / "claimset"), "--version"],
                             capture_output=True, text=True)
        self.assertIn(f"spec {SPEC_VERSION}", lab.stdout)
        sub = self.dir / "claimset-submit"
        val = subprocess.run([sys.executable, "-I", str(sub / "scripts" / "validate.py"),
                              str(sub / "examples" / "submission-native.json")], capture_output=True, text=True)
        self.assertEqual(val.returncode, 0, val.stdout + val.stderr)


if __name__ == "__main__":
    unittest.main()
