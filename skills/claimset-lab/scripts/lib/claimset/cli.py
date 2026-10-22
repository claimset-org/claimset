"""Command-line interface: `claimset <command> ...`."""

import argparse
import json
import sys
from pathlib import Path

from . import LAB_SPEC_VERSION, SPEC_VERSION, __version__
from . import bundle as _bundle
from . import render as _render
from . import validate as _validate
from .lab import cli as _lab_cli


def _cmd_validate(args):
    try:
        with open(args.file, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    rep = _validate.validate(doc, published=args.published, local_root=_validate.claimset_folder(args.file))
    _validate.print_report(rep, published=args.published, as_json=args.json)
    return 1 if rep.errors else 0


def _cmd_tag(args):
    """Mark a version of a paper with a git tag claimset/<study>/<paper>/v<n> (the version as committed)."""
    import subprocess
    from .lab.layout import parse_paper_path

    parsed = parse_paper_path(args.file)
    if parsed is None:
        print(f"ERROR: {args.file} is not a paper of a study: claimset/<study>/<paper>.claimset.json",
              file=sys.stderr)
        return 1
    folder, study, name = parsed
    path = Path(args.file).resolve()

    def git(*a):
        return subprocess.run(["git", *a], cwd=folder, capture_output=True, text=True)
    if git("ls-files", "--error-unmatch", str(path)).returncode != 0:
        print(f"ERROR: {args.file} is not committed; commit it first", file=sys.stderr)
        return 1
    if git("status", "--porcelain", "--", str(path)).stdout.strip():
        print(f"ERROR: {args.file} has uncommitted changes; commit them first", file=sys.stderr)
        return 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    rep = _validate.validate(doc, local_root=folder)
    if rep.errors:
        print(f"ERROR: {args.file} does not validate ({len(rep.errors)} errors); run `claimset validate`",
              file=sys.stderr)
        return 1
    prefix = f"claimset/{study}/{name}/v"
    existing = [t for t in git("tag", "--list", f"{prefix}*").stdout.split() if t[len(prefix):].isdigit()]
    tag = f"{prefix}{1 + max([int(t[len(prefix):]) for t in existing] or [0])}"
    message = args.message or f"{study}/{name}: version {tag.rsplit('v', 1)[1]}"
    out = git("tag", "-a", tag, "-m", message)
    if out.returncode != 0:
        print(f"ERROR: git tag failed: {out.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"{tag} (push it with `git push origin {tag}`)")
    return 0


def _cmd_render(args):
    try:
        with open(args.file, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    rep = _validate.validate(doc, published=args.published)
    if rep.errors:
        print(f"ERROR: {args.file} does not validate ({len(rep.errors)} errors); the reading view is made only "
              f"from a valid file. Run `claimset validate {args.file}`.", file=sys.stderr)
        return 1
    text = _render.render(doc)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="claimset",
        description="Tools for Claimset research papers written by and for LLMs.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"claimset {__version__} (spec {SPEC_VERSION}, lab record {LAB_SPEC_VERSION})",
    )
    sub = p.add_subparsers(dest="command", metavar="command")

    v = sub.add_parser("validate", help="validate a claimset file against the specification")
    v.add_argument("file", help="path to the claimset JSON file")
    v.add_argument("--published", action="store_true",
                   help="check a published file instead of a submission")
    v.add_argument("--json", action="store_true", help="machine-readable report")
    v.set_defaults(func=_cmd_validate)

    rv = sub.add_parser("render", help="the reading view of a claimset: plain text for reading models")
    rv.add_argument("file", help="path to the claimset JSON file")
    rv.add_argument("-o", "--output", help="write the reading view to this file")
    rv.add_argument("--published", action="store_true", help="the file is a published claimset")
    rv.set_defaults(func=_cmd_render)

    tg = sub.add_parser("tag", help="mark the committed version of a paper with a git tag "
                                    "(claimset/<study>/<paper>/v<n>)")
    tg.add_argument("file", help="claimset/<study>/<paper>.claimset.json")
    tg.add_argument("-m", "--message", help="tag message")
    tg.set_defaults(func=_cmd_tag)

    _bundle.add_parsers(sub)
    _lab_cli.add_parser(sub)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return _lab_cli.run(args.func, args)


if __name__ == "__main__":
    sys.exit(main())
