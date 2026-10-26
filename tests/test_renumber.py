import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claimset.renumber import published, renumber  # noqa: E402
from claimset.validate import validate  # noqa: E402


def load(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class TestRenumber(unittest.TestCase):
    """Spec §9, Order and IDs: renumbering in reading order before the first submission."""

    def setUp(self):
        self.d = load("submission-native.json")

    def scrambled(self):
        d = copy.deepcopy(self.d)
        names = {c["id"]: f"C{n}" for c, n in zip(d["claims"], (41, 17, 9))}
        text = json.dumps(d)
        for old, new in names.items():
            text = text.replace(f'"{old}"', f'"__{new}"')
        return json.loads(text.replace('"__C', '"C'))

    def test_ids_are_gapless_and_in_reading_order(self):
        out, mapping = renumber(self.scrambled())
        ids = [c["id"] for c in out["claims"]]
        self.assertEqual(ids, [f"C{n}" for n in range(1, len(ids) + 1)])
        kinds = [0 if c["role"] == "principal" else 3 if any(s.get("kind") == "assumption" for s in c["support"])
                 else 1 for c in out["claims"]]
        self.assertEqual(kinds, sorted(kinds))

    def test_renumbered_file_still_validates(self):
        d = self.scrambled()
        before = validate(d)
        out, _ = renumber(d)
        after = validate(out)
        self.assertEqual(len(after.errors), len(before.errors))

    def test_lab_event_ids_are_left_alone(self):
        d = load("submission-from-lab.json")
        events = [c.get("hypothesis", {}).get("lab_event") for c in d["claims"]]
        out, _ = renumber(d)
        self.assertEqual(sorted(filter(None, events)),
                         sorted(filter(None, (c.get("hypothesis", {}).get("lab_event") for c in out["claims"]))))

    def test_published_files_are_refused(self):
        self.assertTrue(published(load("published-native.json")))
        self.assertFalse(published(self.d))
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "p.json"
            f.write_text(json.dumps(load("published-native.json")), encoding="utf-8")
            res = subprocess.run([sys.executable, "-m", "claimset", "renumber", str(f)], capture_output=True,
                                 text=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src")})
            self.assertEqual(res.returncode, 1)
            self.assertIn("IDs never change", res.stderr)


if __name__ == "__main__":
    unittest.main()
