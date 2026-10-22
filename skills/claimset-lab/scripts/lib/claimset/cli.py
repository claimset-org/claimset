"""Command-line interface: `claimset <command> ...`."""

import argparse
import json
import sys
from pathlib import Path

from . import LAB_SPEC_VERSION, SPEC_VERSION, __version__
from . import bundle as _bundle
from . import render as _render
from . import revision as _revision
from . import validate as _validate
from .lab import cli as _lab_cli


def _cmd_validate(args):
    try:
        with open(args.file, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    rep = _validate.validate(doc, published=args.published, local_root=_validate.claimset_folder(args.file),
                             revision=args.revision)
    _validate.print_report(rep, published=args.published, as_json=args.json)
    return 1 if rep.errors else 0


def _cmd_setup(args):
    """Create, attach or adopt study branches (lab record spec §3)."""
    from .lab import record
    from .lab.core import LabError
    try:
        if args.ci:
            print(f"wrote {record.write_ci()}: pull requests fail if they bring study records into the code")
        if args.study:
            lab, what, notes = record.setup(args.study, title=args.title, repository=args.repository,
                                            remote=args.remote, adopt=args.adopt,
                                            by=args.by or __import__("os").environ.get("CLAIMSET_ACTOR"))
            print(f"{args.study}: {what} at {lab.root.parent} (branch {record.PREFIX}{args.study})")
            for n in notes:
                print(f"  {n}")
        elif not args.ci:
            done = record.attach_all(remote=args.remote)
            for study, what in done:
                print(f"{study}: {what}")
            if not done:
                print("no study branches on the remote; create one with `claimset setup --study NAME --title …`")
        return 0
    except LabError as exc:
        print(f"ERROR [{exc.rule}] {exc.message}", file=sys.stderr)
        return 1


def _cmd_studies(args):
    from .lab import record
    rows = record.studies()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no studies in this repository")
        return 0
    for r in rows:
        if not r.get("attached"):
            print(f"{r['study']}: branch {r['branch']}, not checked out (`claimset setup --study {r['study']}`)")
            continue
        state = [f"{r.get('events', '?')} events", f"{r.get('open', 0)} open hypotheses",
                 f"{r.get('pending', 0)} pending plans"]
        if not r.get("on_branch"):
            state.append("NOT on its study branch: `claimset setup --study " + r["study"] + " --adopt`")
        elif r.get("unpushed"):
            state.append(f"{r['unpushed']} commits not pushed")
        print(f"{r['study']}: " + ", ".join(state))
    print("Record every run with `claimset lab run`, choices with `claimset lab decide`; implementation work "
          "belongs to the study too.")
    return 0


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
        return subprocess.run(["git", *a], cwd=path.parent, capture_output=True, text=True)
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
    v.add_argument("--revision", action="store_true",
                   help="the file is a revision submission, naming the claimset it revises in paper.id")
    v.add_argument("--json", action="store_true", help="machine-readable report")
    v.set_defaults(func=_cmd_validate)

    rv = sub.add_parser("render", help="the reading view of a claimset: plain text for reading models")
    rv.add_argument("file", help="path to the claimset JSON file")
    rv.add_argument("-o", "--output", help="write the reading view to this file")
    rv.add_argument("--published", action="store_true", help="the file is a published claimset")
    rv.set_defaults(func=_cmd_render)

    su = sub.add_parser("setup", help="create, attach or adopt study branches (claimset/<study>), checked out "
                                      "at claimset/<study>/")
    su.add_argument("--study", help="the study (default: attach every study branch of the remote)")
    su.add_argument("--title", help="working title, for a new study")
    su.add_argument("--repository", help="URI of the repository, for a new study")
    su.add_argument("--remote", default="origin")
    su.add_argument("--adopt", action="store_true",
                    help="move a study committed on the code branch (claimset/<study>/) onto its own branch")
    su.add_argument("--by", help="actor recording the move, for --adopt (default: $CLAIMSET_ACTOR)")
    su.add_argument("--ci", action="store_true", help="also write a GitHub workflow that keeps study records out "
                                                       "of pull requests")
    su.set_defaults(func=_cmd_setup)

    sd = sub.add_parser("studies", help="the studies of this repository and their state")
    sd.add_argument("--json", action="store_true")
    sd.set_defaults(func=_cmd_studies)

    tg = sub.add_parser("tag", help="mark the committed version of a paper with a git tag "
                                    "(claimset/<study>/<paper>/v<n>)")
    tg.add_argument("file", help="claimset/<study>/<paper>.claimset.json")
    tg.add_argument("-m", "--message", help="tag message")
    tg.set_defaults(func=_cmd_tag)

    _revision.add_parser(sub)
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
