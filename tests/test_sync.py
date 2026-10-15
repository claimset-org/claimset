"""The copies bundled in skills/ must match their sources."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("sync_skills", ROOT / "tools" / "sync_skills.py")
sync_skills = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_skills)


class TestSkillCopies(unittest.TestCase):
    def test_skill_copies_match_sources(self):
        diff = sync_skills.differences()
        self.assertEqual(
            diff, [],
            msg="Run `python3 tools/sync_skills.py`: " + ", ".join(str(d.relative_to(ROOT)) for _, d in diff),
        )

    def test_bundled_validator_runs_standalone(self):
        import subprocess
        import sys
        script = ROOT / "skills" / "claimset-submit" / "scripts" / "validate.py"
        example = ROOT / "skills" / "claimset-submit" / "examples" / "submission-native.json"
        out = subprocess.run([sys.executable, "-I", str(script), str(example)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
