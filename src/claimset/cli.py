"""Command-line interface: `claimset <command> ...`."""

import argparse
import json
import sys

from . import LAB_SPEC_VERSION, SPEC_VERSION, __version__
from . import validate as _validate
from .lab import cli as _lab_cli


def _cmd_validate(args):
    try:
        with open(args.file, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    rep = _validate.validate(doc, published=args.published)
    _validate.print_report(rep, published=args.published, as_json=args.json)
    return 1 if rep.errors else 0


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
