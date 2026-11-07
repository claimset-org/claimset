"""Find places where the validator fails on a value of the wrong type instead of reporting it.

Every field of every file in tests/compat/ (all versions) is replaced, one at a time, by a list, an object, a number,
null, an empty string or a boolean, and the file is validated. A crash, or a rule-86 report (the validator's last
resort when it stops on a type it did not expect), is a bug: the rule of that field should report it. Prints one line
per place in the code, with the first input that reached it, and exits 1 if there is any.

    python3 tools/fuzz_types.py            # all versions; takes a few minutes
    python3 tools/fuzz_types.py 0.39       # one version
"""

import copy
import json
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claimset.validate import validate  # noqa: E402

ODD = [["E1"], {"x": 1}, 7, None, "", True, [], {}]
AT = re.compile(r"at (\S+), line (\d+) of (\S+)\)")


def paths(x, p=()):
    yield p
    if isinstance(x, dict):
        for k, v in x.items():
            yield from paths(v, p + (k,))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from paths(v, p + (i,))


def put(d, p, v):
    for k in p[:-1]:
        d = d[k]
    d[p[-1]] = v


def main(versions):
    found = {}
    for f in sorted(p for v in versions for p in (ROOT / "tests" / "compat" / v).glob("*.json")):
        base = json.loads(f.read_text(encoding="utf-8"))
        where = f"{f.parent.name}/{f.name}"
        for p in list(paths(base))[1:]:
            for v in ODD:
                d = copy.deepcopy(base)
                put(d, p, v)
                try:
                    report = validate(d)
                except Exception as exc:                       # noqa: BLE001 - a crash is what we look for
                    tb = traceback.extract_tb(exc.__traceback__)[-1]
                    key = (Path(tb.filename).name, tb.lineno, tb.name, "crash")
                else:
                    net = next((e for e in report.errors if e["rule"] == "86"), None)
                    if net is None:
                        continue
                    m = AT.search(net["message"])
                    key = (m.group(3), int(m.group(2)), m.group(1), "rule 86") if m else ("?", 0, "?", "rule 86")
                found.setdefault(key, (where, ".".join(map(str, p)), json.dumps(v)))
    for (file, line, func, how), (where, field, value) in sorted(found.items()):
        print(f"{file}:{line} in {func} ({how}): {where} {field} = {value}")
    print(f"{len(found)} place(s)")
    return 1 if found else 0


if __name__ == "__main__":
    vs = sys.argv[1:] or sorted(p.name for p in (ROOT / "tests" / "compat").iterdir() if p.is_dir())
    sys.exit(main(vs))
