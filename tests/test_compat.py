"""Compatibility between specification versions (claimset spec §2.6, rule 79)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claimset.validate import BASELINE_VERSION, SCHEMA_VERSION, schema_version_tuple, validate  # noqa: E402

COMPAT = ROOT / "tests" / "compat"


def rules(report):
    return {e["rule"] if isinstance(e, dict) else e[0] for e in report.errors}


def example(name="submission-native.json"):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class TestCompatibilityFiles(unittest.TestCase):
    def test_every_version_from_the_baseline_stays_valid(self):
        folders = sorted(p for p in COMPAT.iterdir() if p.is_dir())
        self.assertIn(BASELINE_VERSION, [p.name for p in folders])
        for folder in folders:
            for f in sorted(folder.glob("*.json")):
                doc = json.loads(f.read_text(encoding="utf-8"))
                self.assertEqual(doc["schema_version"], folder.name, f)
                report = validate(doc, published=f.name.startswith("published-"))
                self.assertEqual(report.errors, [], f"{f.relative_to(ROOT)} is no longer valid")

    def test_the_current_version_has_compatibility_files(self):
        self.assertTrue(list((COMPAT / SCHEMA_VERSION).glob("*.json")))


class TestRule79(unittest.TestCase):
    def test_a_newer_version_is_refused_as_unknown(self):
        major, minor = schema_version_tuple(SCHEMA_VERSION)
        doc = example()
        doc["schema_version"] = f"{major}.{minor + 1}"
        report = validate(doc)
        self.assertEqual(rules(report), {"79"})
        self.assertIn("newer than this validator", str(report.errors))

    def test_an_older_version_is_refused_with_migrate(self):
        doc = example()
        doc["schema_version"] = "0.36"
        report = validate(doc)
        self.assertIn("79", rules(report))
        self.assertIn("claimset migrate", str(report.errors))

    def test_not_a_version(self):
        for bad in (None, 37, "v0.37", "0.37.1", "0.037", ""):
            doc = example()
            doc["schema_version"] = bad
            self.assertIn("79", rules(validate(doc)), bad)

    def test_versions_compare_as_numbers(self):
        self.assertGreater(schema_version_tuple("0.100"), schema_version_tuple("0.37"))
        self.assertGreater(schema_version_tuple("1.0"), schema_version_tuple("0.99"))


class TestFreezing(unittest.TestCase):
    """A rehearsal of a version change on a copy of the package: freeze, raise the version, add a rule. Files of the
    old version keep being checked by the old rules; files of the new version get the new rule."""

    def test_a_frozen_version_keeps_its_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copytree(ROOT / "src", tmp / "src", ignore=shutil.ignore_patterns("__pycache__", "data"))
            shutil.copytree(ROOT / "tools", tmp / "tools", ignore=shutil.ignore_patterns("__pycache__"))
            out = subprocess.run([sys.executable, str(tmp / "tools" / "freeze_validator.py")], capture_output=True,
                                 text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            major, minor = schema_version_tuple(SCHEMA_VERSION)
            new = f"{major}.{minor + 1}"
            v = tmp / "src" / "claimset" / "validate.py"
            text = v.read_text(encoding="utf-8")
            text = text.replace(f'SCHEMA_VERSION = "{SCHEMA_VERSION}"', f'SCHEMA_VERSION = "{new}"', 1)
            # a rule of the new version: every file must have a field `novelty_rule`
            text = text.replace('    for key in ("paper", "contributors", "models", "claims"):',
                                '    if "novelty_rule" not in doc:\n'
                                '        r.err("99", "novelty_rule", "required from the new version")\n'
                                '    for key in ("paper", "contributors", "models", "claims"):', 1)
            v.write_text(text, encoding="utf-8")
            script = (
                "import json, sys\n"
                "from claimset.validate import validate\n"
                "doc = json.load(open(sys.argv[1]))\n"
                "old = validate(doc)\n"
                f"doc['schema_version'] = '{new}'\n"
                "new = validate(doc)\n"
                "print(json.dumps([len(old.errors), sorted({e['rule'] if isinstance(e, dict) else e[0] "
                "for e in new.errors})]))\n")
            env = {**os.environ, "PYTHONPATH": str(tmp / "src")}
            out = subprocess.run([sys.executable, "-c", script, str(ROOT / "examples" / "submission-native.json")],
                                 capture_output=True, text=True, env=env, cwd=tmp)
            self.assertEqual(out.returncode, 0, out.stderr)
            old_errors, new_rules = json.loads(out.stdout)
            self.assertEqual(old_errors, 0)              # the old version: checked by its frozen rules
            self.assertEqual(new_rules, ["99"])          # the new version: the new rule applies
            again = subprocess.run([sys.executable, str(tmp / "tools" / "freeze_validator.py")],
                                   capture_output=True, text=True)
            self.assertEqual(again.returncode, 0, again.stderr)   # freezes the new version in turn


if __name__ == "__main__":
    unittest.main()


class TestLabRuleL29(unittest.TestCase):
    def test_a_record_begun_under_a_later_version_is_refused(self):
        from claimset import LAB_SPEC_VERSION
        from claimset.lab import core
        major, minor = core.version_tuple(LAB_SPEC_VERSION)[:2]
        with self.assertRaises(core.LabError) as cm:
            core.check_init(None, {"lab_spec_version": f"{major}.{minor + 1}", "working_title": "x"}, None, "INIT")
        self.assertEqual(cm.exception.rule, "L29")
        self.assertIn("newer than this tool", cm.exception.message)
        for ok in (LAB_SPEC_VERSION, "0.5", "0.1"):              # the tool's own and earlier versions are read
            core.check_init(None, {"lab_spec_version": ok, "working_title": "x"}, None, "INIT")
