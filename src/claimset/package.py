"""Building a submission package (spec §2.5): a BagIt bag (RFC 8493) holding the claimset and the files its
artefacts name by relative path.

    claimset package claimset/<study>/<paper>.claimset.json -o <paper>.zip

Each artefact whose `uri` is a relative path (`files/fig3.png`) is taken from beside the claimset file, copied to
`data/<uri>` in the bag, and its `version` set to `sha256:` followed by the file's checksum. The claimset is written
to `data/<name>`, and `bagit.txt` and `manifest-sha256.txt` list every file of the payload with its checksum.
"""

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .validate import is_relative_uri

BAGIT_TXT = "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"


class PackageError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe(uri):
    """A relative path that stays inside the payload."""
    p = PurePosixPath(uri)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise PackageError(f"'{uri}': a packaged file's uri is a relative path inside the claimset's folder")
    return p


def build(claimset_path, out, update_source=False):
    """Build the bag at `out` (a folder, or a .zip file). Returns (changes, files): the artefacts whose `version`
    was set or corrected, and the packaged files. With `update_source`, the versions are also written back to the
    claimset file."""
    src = Path(claimset_path)
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read {src}: {exc}")
    base = src.parent
    out = Path(out)
    as_zip = out.suffix == ".zip"
    work = Path(tempfile.mkdtemp()) / (out.stem if as_zip else out.name)
    changes, files = [], []
    try:
        payload = work / "data"
        payload.mkdir(parents=True)
        for a in doc.get("artefacts") or []:
            if not isinstance(a, dict) or not is_relative_uri(a.get("uri")):
                continue
            rel = _safe(a["uri"])
            f = base.joinpath(*rel.parts)
            if not f.is_file():
                raise PackageError(f"artefact {a.get('id')}: {a['uri']} is not a file beside {src.name}")
            dest = payload.joinpath(*rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            version = f"sha256:{sha256(dest)}"
            if a.get("version") != version:
                changes.append(f"{a.get('id')}: version {a.get('version') or '(none)'} -> {version}")
                a["version"] = version
            files.append(str(rel))
        text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
        if str(src.name) in files:
            raise PackageError(f"an artefact uses the claimset's own name, {src.name}")
        (payload / src.name).write_text(text, encoding="utf-8")
        (work / "bagit.txt").write_text(BAGIT_TXT, encoding="utf-8")
        lines = [f"{sha256(p)}  {p.relative_to(work).as_posix()}"
                 for p in sorted(payload.rglob("*")) if p.is_file()]
        (work / "manifest-sha256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if out.exists():
            raise PackageError(f"{out} exists; choose another output")
        if as_zip:
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for p in sorted(work.rglob("*")):
                    if p.is_file():
                        z.write(p, (PurePosixPath(work.name) / p.relative_to(work).as_posix()).as_posix())
        else:
            shutil.copytree(work, out)
        if update_source and changes:
            src.write_text(text, encoding="utf-8")
    finally:
        shutil.rmtree(work.parent, ignore_errors=True)
    return changes, files
