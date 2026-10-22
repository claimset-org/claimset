"""The specifications and agent skills that match this package (`claimset spec`, `claimset skill ...`).

The package carries the texts of its specifications, the examples and the skills' instructions, so an
installed package can always print the specification it validates against and install skills that use
it. A skill copied into a project once does not follow later versions; `claimset skill check` finds such
copies and `claimset skill install` replaces them.
"""

import filecmp
import shutil
import sys
import tempfile
from pathlib import Path

from . import LAB_SPEC_VERSION, REVISION_SPEC_VERSION, SPEC_VERSION, __version__

PACKAGE = Path(__file__).resolve().parent
DATA = PACKAGE / "data"
SKILLS = ("claimset-submit", "claimset-lab", "claimset-revise")
DEFAULT_DIR = ".claude/skills"


def spec_path(lab=False, revision=False):
    name = f"claimset-revision-v{REVISION_SPEC_VERSION}.md" if revision else \
        f"lab-record-v{LAB_SPEC_VERSION}.md" if lab else f"claimset-v{SPEC_VERSION}.md"
    path = DATA / "spec" / name
    if not path.exists():
        raise FileNotFoundError(f"this copy of claimset ({__version__}) carries no specification texts; install "
                                f"the package (pip install git+https://github.com/claimset-org/claimset)")
    return path


def _package_modules():
    """The package's Python files, relative to the package directory."""
    return [f.relative_to(PACKAGE) for f in sorted(PACKAGE.rglob("*.py")) if "__pycache__" not in f.parts]


def files(name):
    """{path inside the skill: source file} for one skill, as this package would install it."""
    spec, lab, rev = spec_path(), spec_path(lab=True), spec_path(revision=True)
    rev_name = f"references/{rev.name.replace('claimset-revision-v', 'revision-v')}"
    if name == "claimset-submit":
        out = {"SKILL.md": DATA / "skills" / name / "SKILL.md",
               "scripts/validate.py": PACKAGE / "validate.py",
               f"references/{spec.name.replace('claimset-v', 'spec-v')}": spec,
               f"references/{lab.name}": lab}
        for ex in sorted((DATA / "examples").glob("*.json")):
            out[f"examples/{ex.name}"] = ex
        return out
    if name in ("claimset-lab", "claimset-revise"):
        out = {"SKILL.md": DATA / "skills" / name / "SKILL.md",
               "scripts/claimset": DATA / "skills" / "claimset-lab" / "claimset",
               f"references/{lab.name}": lab,
               rev_name: rev}
        if name == "claimset-revise":
            out[f"references/{spec.name.replace('claimset-v', 'spec-v')}"] = spec
        for rel in _package_modules():
            out[f"scripts/lib/claimset/{rel.as_posix()}"] = PACKAGE / rel
        return out
    raise ValueError(f"unknown skill '{name}': the skills are {', '.join(SKILLS)}")


def _is_claimset_skill(directory, name):
    md = directory / "SKILL.md"
    return md.exists() and f"name: {name}" in md.read_text(encoding="utf-8", errors="replace")[:500]


def check(directory, name):
    """Differences between an installed skill and this package's: [(path, what)]; empty when current."""
    target = Path(directory) / name
    if not target.exists():
        return [(name, "not installed")]
    want = files(name)
    out = []
    for rel, src in want.items():
        dst = target / rel
        if not dst.exists():
            out.append((rel, "missing"))
        elif not filecmp.cmp(src, dst, shallow=False):
            out.append((rel, "differs"))
    for f in sorted(target.rglob("*")):
        rel = f.relative_to(target).as_posix()
        if f.is_file() and "__pycache__" not in f.parts and rel not in want:
            out.append((rel, "not part of this version"))
    return out


def install(directory, name, force=False):
    """Install one skill into directory/name, replacing an earlier copy of the same skill. Returns
    "installed", "replaced" or "current"."""
    target = Path(directory) / name
    if target.exists():
        if not check(directory, name):
            return "current"
        if not (force or _is_claimset_skill(target, name)):
            raise FileExistsError(f"{target} exists and is not the {name} skill; use --force to replace it")
    Path(directory).mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=str(Path(directory).resolve())))
    try:
        for rel, src in files(name).items():
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        if name in ("claimset-lab", "claimset-revise"):
            (staging / "scripts" / "claimset").chmod(0o755)
        existed = target.exists()
        if existed:
            shutil.rmtree(target)
        staging.rename(target)
        return "replaced" if existed else "installed"
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def cmd_spec(args):
    try:
        path = spec_path(lab=args.lab, revision=args.revision)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"{path.name} written to {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_skill(args):
    names = args.names or list(SKILLS)
    try:
        if args.action == "check":
            stale = 0
            for name in names:
                diff = check(args.dir, name)
                if not diff:
                    print(f"{name}: current (claimset {__version__}, spec {SPEC_VERSION})")
                    continue
                stale += 1
                print(f"{name}: out of date with claimset {__version__} (spec {SPEC_VERSION}); "
                      f"run `claimset skill install --dir {args.dir}`")
                for rel, what in diff[:20]:
                    print(f"  {rel}: {what}")
            return 1 if stale else 0
        for name in names:
            print(f"{name}: {install(args.dir, name, force=args.force)} in {Path(args.dir) / name} "
                  f"(claimset {__version__}, spec {SPEC_VERSION})")
        return 0
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def add_parsers(sub):
    s = sub.add_parser("spec", help="print the specification this package validates against")
    s.add_argument("--lab", action="store_true", help="the lab record specification instead")
    s.add_argument("--revision", action="store_true",
                   help="the revision specification (reviews, responses, revised versions, scores) instead")
    s.add_argument("-o", "--output", help="write it to this file")
    s.set_defaults(func=cmd_spec)

    k = sub.add_parser("skill", help="install or check the agent skills that match this package")
    k.add_argument("action", choices=["install", "check"])
    k.add_argument("names", nargs="*", metavar="skill", help=f"{', '.join(SKILLS)} (default: all)")
    k.add_argument("--dir", default=DEFAULT_DIR, help=f"the skills directory (default: {DEFAULT_DIR})")
    k.add_argument("--force", action="store_true", help="replace a directory that is not a claimset skill")
    k.set_defaults(func=cmd_skill)
