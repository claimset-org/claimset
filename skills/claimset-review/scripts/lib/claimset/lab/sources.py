"""Captured sources (lab record spec v0.16 §5.20): outside material kept as data.

A page, a post, a transcript or a document enters the record as a `source` event: the file as the researcher
saved it (or as the tool fetched it), its hash, where and how it was obtained, whose it is and whether it may be
published. Its text is what excerpts are checked against: quotes come from captured sources, as numbers come
from code (§2).
"""

import hashlib
import html.parser
import mimetypes
import secrets
import re
import shutil
import urllib.request
import uuid
from pathlib import Path

from .core import LabError, file_hash

SOURCES_DIR = "sources"
PRIVATE_DIR = "private"                          # lab/private/: kept out of git (§5.20, "Private sources")
PRIVATE_IGNORE = ("# Kept out of git: the files of private sources and the pseudonym salt (lab record §5.20).\n"
                  "# The record holds their hashes; copy this folder to another checkout to verify them there.\n*\n")
TEXT_FILE = "text.txt"
TEXT_FORMATS = ("text/plain", "text/markdown", "text/csv", "application/json")
HTML_FORMATS = ("text/html", "application/xhtml+xml")
CONTEXT = 32                                     # characters of prefix and suffix in a quote's selector


class _Text(html.parser.HTMLParser):
    """The visible text of an HTML page: script, style and head left out; block elements end lines."""
    SKIP = {"script", "style", "head", "noscript", "template", "svg"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "article",
             "section", "header", "footer", "td", "th", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))   # line breaks in markup are spaces; blocks end lines


def html_text(markup):
    p = _Text()
    p.feed(markup)
    p.close()
    lines = (" ".join(l.split()) for l in "".join(p.out).splitlines())
    return "\n".join(l for l in lines if l) + "\n"


def guess_format(path):
    fmt, _ = mimetypes.guess_type(str(path))
    return fmt or "application/octet-stream"


def _open(url, timeout):                         # replaced in tests
    req = urllib.request.Request(url, headers={"User-Agent": "claimset-lab"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch(url, dest_dir, timeout=30):
    """Fetch a public page into dest_dir; return (path, media type)."""
    try:
        with _open(url, timeout) as resp:
            data = resp.read()
            fmt = (resp.headers.get_content_type() if hasattr(resp, "headers") and resp.headers else None)
    except Exception as exc:  # urllib raises several kinds
        raise LabError("schema", f"capture: could not fetch {url}: {exc}") from None
    fmt = fmt or "application/octet-stream"
    ext = mimetypes.guess_extension(fmt) or ".bin"
    name = "page" + (".html" if fmt in HTML_FORMATS else ext)
    path = Path(dest_dir) / name
    path.write_bytes(data)
    return path, fmt


SALT_FILE = "pseudonym-salt"
PSEUDONYM_RE = re.compile(r"^a-[0-9a-f]{12}$")


def private_root(lab):
    """lab/private/, made with a .gitignore that keeps everything in it (itself included) out of git."""
    d = lab.root / PRIVATE_DIR
    d.mkdir(exist_ok=True)
    ignore = d / ".gitignore"
    if not ignore.exists():
        ignore.write_text(PRIVATE_IGNORE, encoding="utf-8")
    return d


def is_private(body):
    return isinstance(body, dict) and body.get("visibility") != "public"


def source_dir(lab, source):
    """The folder of a source's files: lab/private/sources/<id>/ for a private source, lab/sources/<id>/ for a
    public one. A private source captured before v0.19 has its files in lab/sources/<id>/, committed."""
    sid = str(source.get("id"))
    if is_private(source.get("body")):
        d = lab.root / PRIVATE_DIR / SOURCES_DIR / sid
        old = lab.root / SOURCES_DIR / sid
        return old if not d.is_dir() and old.is_dir() else d
    return lab.root / SOURCES_DIR / sid


def salt_path(lab):
    """The study's pseudonym salt: lab/private/pseudonym-salt, kept out of git; a record begun before v0.19 may
    have it committed as lab/pseudonym-salt, and keeps using it."""
    old = lab.root / SALT_FILE
    return old if old.exists() else lab.root / PRIVATE_DIR / SALT_FILE


def pseudonym(lab, handle):
    """A stable pseudonym for an author's handle in this study (§5.20): `a-` and 12 hex digits of the SHA-256 of
    the study's salt and the handle. The salt is kept out of git, in lab/private/, so the record cannot be used
    to check a guessed handle against a pseudonym; a checkout without it cannot make pseudonyms that match the
    ones already recorded, so it refuses until the salt is copied in."""
    path = salt_path(lab)
    if not path.exists():
        used = sorted({e["id"] for e in lab.read() if e.get("type") == "source" and isinstance(e.get("body"), dict)
                       and PSEUDONYM_RE.match(str(e["body"].get("author") or ""))})
        if used:
            raise LabError("record", f"capture --author: this checkout has no pseudonym salt, and {used[0]} has an "
                                     f"author pseudonym made with the study's salt; copy "
                                     f"lab/private/{SALT_FILE} from the checkout that captured it (it is kept out "
                                     f"of git), or give the author as written with --author-as-given")
        private_root(lab)
        path.write_text(secrets.token_hex(16) + "\n", encoding="utf-8")
    salt = path.read_text(encoding="utf-8").strip()
    norm = " ".join(str(handle).split()).casefold()
    return "a-" + hashlib.sha256(f"{salt}:{norm}".encode("utf-8")).hexdigest()[:12]


def capture(lab, by, file=None, url=None, fetch_url=False, method=None, retrieved_at=None, title=None,
            fmt=None, text=None, visibility=None, holder="third_party", licence=None, terms=None,
            archive_uri=None, venue=None, published_at=None, description=None, author=None,
            author_as_given=False):
    """Record a source: copy (or fetch) the file, hash it, extract its text, write the event. The files go to
    lab/sources/<id>/ for a public source, and to lab/private/sources/<id>/, kept out of git, for a private one."""
    if fetch_url == bool(file):
        raise LabError("schema", "capture: give a saved file, or --fetch with a URL")
    if fetch_url and not url:
        raise LabError("schema", "capture: --fetch needs the URL (--uri)")
    if visibility is None:
        visibility = "private" if holder == "third_party" else "public"
    root = (private_root(lab) / SOURCES_DIR) if visibility == "private" else lab.root / SOURCES_DIR
    root.mkdir(parents=True, exist_ok=True)
    pending = root / f".pending-{uuid.uuid4().hex}"
    pending.mkdir()
    event = None
    try:
        if fetch_url:
            original, fmt_found = fetch(url, pending)
            method = "fetched"
            retrieved_at = lab.clock()
            fmt = fmt or fmt_found
        else:
            src = Path(file)
            if not src.is_file():
                raise LabError("schema", f"capture: no such file {file}")
            original = pending / src.name
            shutil.copyfile(src, original)
            method = method or "saved"
            fmt = fmt or guess_format(src)
        files = [{"path": original.name, "hash": file_hash(original), "role": "original"}]
        if text:
            t = Path(text)
            if not t.is_file():
                raise LabError("schema", f"capture: no such text file {text}")
            shutil.copyfile(t, pending / TEXT_FILE)
        elif fmt in HTML_FORMATS:
            raw = original.read_bytes().decode("utf-8", errors="replace")
            (pending / TEXT_FILE).write_text(html_text(raw), encoding="utf-8")
        if (pending / TEXT_FILE).exists() and original.name != TEXT_FILE:
            files.append({"path": TEXT_FILE, "hash": file_hash(pending / TEXT_FILE), "role": "text"})
        rights = {k: v for k, v in (("holder", holder), ("licence", licence), ("terms", terms)) if v}
        if author is not None and not str(author).strip():
            raise LabError("schema", "capture: --author must name the author")
        who = None if author is None else (" ".join(str(author).split()) if author_as_given else pseudonym(lab, author))
        body = {"uri": url, "title": title, "author": who, "method": method, "retrieved_at": retrieved_at,
                "format": fmt,
                "files": files, "visibility": visibility, "rights": rights, "archive_uri": archive_uri,
                "venue": venue, "published_at": published_at, "description": description}
        event = lab.append("source", {k: v for k, v in body.items() if v is not None}, by=by)
        final = root / event["id"]
        if final.exists():
            raise LabError("L24", f"{final} already exists; the captured files are kept in {pending}")
        pending.rename(final)
        return event
    except BaseException:
        if event is None and pending.exists():     # nothing was recorded: leave nothing behind
            shutil.rmtree(pending, ignore_errors=True)
        raise


def correct(lab, by, old_id, **changes):
    """A correction of a captured source's description (an archived copy found later, a date, the author): the
    same files, copied into the correction's folder, with the given fields changed (§5.20)."""
    st = lab.view(by)
    old = st.latest(old_id) if st.get(old_id) else None
    if old is None or old["type"] != "source":
        raise LabError("L11", f"capture --supersedes: {old_id} is not a source")
    src_dir = source_dir(lab, old)
    if not src_dir.is_dir():
        where = " (a private source's files are kept out of git: copy lab/private/ from the checkout that captured " \
                "it)" if is_private(old["body"]) else ""
        raise LabError("L24", f"capture --supersedes: the files of {old['id']} are missing ({src_dir}){where}")
    author = changes.pop("author", None)
    as_given = changes.pop("author_as_given", False)
    body = dict(old["body"])
    for k, v in changes.items():
        if v is not None:
            body[k] = v
    if author is not None:
        body["author"] = " ".join(str(author).split()) if as_given else pseudonym(lab, author)
    if body.get("visibility") == "public" and (body.get("rights") or {}).get("holder") == "third_party" and \
            not ((body.get("rights") or {}).get("licence") or (body.get("rights") or {}).get("terms")):
        raise LabError("L26", "capture: content written by others is published only under its own licence or terms")
    root = (private_root(lab) / SOURCES_DIR) if is_private(body) else lab.root / SOURCES_DIR
    root.mkdir(parents=True, exist_ok=True)
    pending = root / f".pending-{uuid.uuid4().hex}"
    shutil.copytree(src_dir, pending)
    event = None
    try:
        event = lab.append("source", body, by=by, supersedes=old["id"])
        pending.rename(root / event["id"])
        return event
    except BaseException:
        if event is None and pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        raise


def text_of(lab, source):
    """The text of a source excerpts are checked against: its extracted text, or the original if it is text."""
    body = source["body"]
    files = {f.get("role"): f.get("path") for f in body.get("files") or [] if isinstance(f, dict)}
    d = source_dir(lab, source)
    if not d.is_dir() and is_private(body):
        raise LabError("L24", f"{source['id']} is private and its files are not in this checkout (they are kept "
                              f"out of git): copy lab/private/ from the checkout that captured it")
    if files.get("text"):
        return (d / files["text"]).read_text(encoding="utf-8", errors="replace")
    if files.get("original") and (body.get("format") in TEXT_FORMATS or body.get("format") in HTML_FORMATS):
        raw = (d / files["original"]).read_text(encoding="utf-8", errors="replace")
        return html_text(raw) if body.get("format") in HTML_FORMATS else raw
    raise LabError("schema", f"{source['id']} has no text to quote from (capture it with --text)")


def _flat(s):
    """Whitespace collapsed, with a map from positions in the result to positions in `s`."""
    out, where, space = [], [], False
    for i, ch in enumerate(s):
        if ch.isspace():
            if out and not space:
                out.append(" ")
                where.append(i)
            space = True
        else:
            out.append(ch)
            where.append(i)
            space = False
    return "".join(out), where


def quote(lab, source, words):
    """Find `words` in the source's text (whitespace-insensitive). Returns a W3C Web Annotation selector:
    {source, exact, prefix, suffix, start, end}, with start and end as character offsets in the text. Raises
    LabError if the words are not there, or are there more than once without enough context to tell."""
    text = text_of(lab, source)
    flat, where = _flat(text)
    want = " ".join(str(words).split())
    if not want:
        raise LabError("schema", "quote: the excerpt is empty")
    hits = [m.start() for m in re.finditer(re.escape(want), flat)]
    if not hits:
        raise LabError("L25", f"quote: the words are not in {source['id']}'s text")
    i = hits[0]
    start, end = where[i], where[i + len(want) - 1] + 1
    return {"source": source["id"], "exact": text[start:end], "prefix": text[max(0, start - CONTEXT):start],
            "suffix": text[end:end + CONTEXT], "start": start, "end": end, "occurrences": len(hits)}


def verify_source_files(lab, events):
    """Rule L24: each source's folder holds exactly the files its event records, unchanged."""
    from .core import is_withheld
    errors = []
    for e in events:
        if not isinstance(e, dict) or e.get("type") != "source" or is_withheld(e) or \
                not isinstance(e.get("body"), dict):
            continue
        files = e["body"].get("files")
        if not isinstance(files, list):
            continue
        d = source_dir(lab, e)
        recorded = {f.get("path"): f.get("hash") for f in files if isinstance(f, dict)}
        if not d.is_dir() and is_private(e["body"]):
            continue                              # kept out of git; source_notes says so
        if not d.is_dir():
            errors.append({"seq": e["seq"], "id": e["id"], "rule": "L24", "message": f"source folder {d} is missing"})
            continue
        present = {str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*") if p.is_file()}
        for path, h in recorded.items():
            if path not in present:
                errors.append({"seq": e["seq"], "id": e["id"], "rule": "L24", "message": f"file {path} is missing"})
            elif file_hash(d / path) != h:
                errors.append({"seq": e["seq"], "id": e["id"], "rule": "L24",
                               "message": f"file {path} was changed after it was captured"})
        for path in sorted(present - set(recorded)):
            errors.append({"seq": e["seq"], "id": e["id"], "rule": "L24",
                           "message": f"file {path} was added to the source folder after capture"})
    return errors


def source_notes(lab, events):
    """What verify says about private sources: those whose files are not in this checkout (and so could not be
    checked), those committed before v0.19, and a committed pseudonym salt."""
    from .core import is_withheld
    absent, committed, authored = [], [], False
    for e in events:
        if not isinstance(e, dict) or e.get("type") != "source" or is_withheld(e) or not isinstance(e.get("body"), dict):
            continue
        authored = authored or bool(PSEUDONYM_RE.match(str(e["body"].get("author") or "")))
        if not is_private(e["body"]):
            continue
        d = source_dir(lab, e)
        if not d.is_dir():
            absent.append(str(e.get("id")))
        elif d.parent.parent == lab.root:
            committed.append(str(e.get("id")))
    notes = []
    if absent:
        notes.append(f"the files of private sources {', '.join(absent)} are not in this checkout (they are kept out "
                     f"of git), so their hashes and the quotes from them were not checked: copy lab/private/ from "
                     f"the checkout that captured them")
    if committed:
        notes.append(f"private sources {', '.join(committed)} have their files committed in lab/sources/ (captured "
                     f"before lab record v0.19): the branch's history keeps them, so its remote must be as private "
                     f"as they are")
    if authored and (lab.root / SALT_FILE).exists():
        notes.append(f"the pseudonym salt is committed (lab/{SALT_FILE}, from before lab record v0.19): anyone with "
                     f"the record can check a guessed handle against the pseudonyms")
    elif authored and not salt_path(lab).exists():
        notes.append(f"this checkout has no pseudonym salt (lab/private/{SALT_FILE}): copy it from the checkout "
                     f"that captured the sources before capturing more with --author")
    return notes


CODINGS_FILE = "codings.json"


def verify_codings(lab, events):
    """Rule L25: every coding's quote is in its source's text, at the recorded place."""
    from .core import State, is_withheld
    st = State()
    for e in events:
        if isinstance(e, dict):
            st.apply(e)
    errors = []
    for e in events:
        if not isinstance(e, dict) or e.get("type") != "coding" or is_withheld(e) or not isinstance(e.get("body"), dict):
            continue
        sel = e["body"].get("quote")
        src = st.get(e["body"].get("source"))
        if not isinstance(sel, dict) or src is None or is_withheld(src):
            continue
        if is_private(src.get("body")) and not source_dir(lab, src).is_dir():
            continue                              # kept out of git; source_notes says so
        try:
            text = text_of(lab, src)
        except (LabError, OSError):
            errors.append({"seq": e["seq"], "id": e["id"], "rule": "L25",
                           "message": f"the text of {src['id']} cannot be read, so the quote cannot be checked"})
            continue
        start, end = sel.get("start"), sel.get("end")
        if not (isinstance(start, int) and isinstance(end, int) and text[start:end] == sel.get("exact")):
            errors.append({"seq": e["seq"], "id": e["id"], "rule": "L25",
                           "message": f"the quote is not in {src['id']}'s text at {start}-{end}"})
    return errors


def current_codings(st):
    """The codings in force in a view of the record: the latest version of each, without withdrawn or withheld
    ones, in record order."""
    from .core import is_withheld
    out = []
    for e in st.events:
        if e.get("type") != "coding" or st.root(e["id"]) != e["id"]:
            continue
        cur = st.latest(e["id"])
        if cur is None or is_withheld(cur) or cur["body"].get("withdrawn") or \
                not (cur["body"].get("codes") or cur["body"].get("none_apply")):
            continue
        out.append(cur)
    return out


def codings_snapshot(st):
    """What a run reads in $CLAIMSET_CODINGS (§5.21): the codebook, the sources coded and the codings in force,
    as the run's writer sees them. None if the view has no coding."""
    from .core import is_withheld
    codings = current_codings(st)
    if not codings:
        return None
    codes, sources = {}, {}
    for c in codings:
        for t in c["body"]["codes"]:
            term = st.latest(t)
            root = st.root(t)
            if root not in codes and term is not None and not is_withheld(term):
                tb = term["body"]
                codes[root] = {"id": root, "version": term["id"], "term": tb.get("term"),
                               "definition": tb.get("definition"), "code": tb.get("code"),
                               "broader": [st.root(b) for b in tb.get("broader") or []]}
        s = st.get(c["body"]["source"])
        if s is not None and not is_withheld(s) and s["id"] not in sources:
            b = s["body"]
            sources[s["id"]] = {k: b.get(k) for k in ("uri", "title", "author", "venue", "published_at",
                                                      "retrieved_at") if b.get(k) is not None}
            sources[s["id"]]["id"] = s["id"]
    rows = []
    for c in codings:
        b = c["body"]
        row = {"id": c["id"], "root": st.root(c["id"]), "source": b["source"],
               "codes": [st.root(t) for t in b["codes"]], "code_versions": b.get("code_versions"),
               "coder": c.get("by"), "at": c.get("at")}
        for k in ("unit", "pass", "memo"):
            if b.get(k) is not None:
                row[k] = b[k]
        if b.get("none_apply"):
            row["none_apply"] = True
        if isinstance(b.get("quote"), dict):
            row["quote"] = {k: b["quote"][k] for k in ("exact", "start", "end") if k in b["quote"]}
        rows.append(row)
    return {"codes": list(codes.values()), "sources": list(sources.values()), "codings": rows}
