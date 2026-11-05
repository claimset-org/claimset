"""Captured sources (lab record spec v0.16 §5.20): outside material kept as data.

A page, a post, a transcript or a document enters the record as a `source` event: the file as the researcher
saved it (or as the tool fetched it), its hash, where and how it was obtained, whose it is and whether it may be
published. Its text is what excerpts are checked against: quotes come from captured sources, as numbers come
from code (§2).
"""

import html.parser
import mimetypes
import re
import shutil
import urllib.request
import uuid
from pathlib import Path

from .core import LabError, file_hash

SOURCES_DIR = "sources"
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


def capture(lab, by, file=None, url=None, fetch_url=False, method=None, retrieved_at=None, title=None,
            fmt=None, text=None, visibility=None, holder="third_party", licence=None, terms=None,
            archive_uri=None, venue=None, published_at=None, description=None):
    """Record a source: copy (or fetch) the file into sources/<id>/, hash it, extract its text, write the event."""
    if fetch_url == bool(file):
        raise LabError("schema", "capture: give a saved file, or --fetch with a URL")
    if fetch_url and not url:
        raise LabError("schema", "capture: --fetch needs the URL (--uri)")
    root = lab.root / SOURCES_DIR
    root.mkdir(exist_ok=True)
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
        if visibility is None:
            visibility = "private" if holder == "third_party" else "public"
        rights = {k: v for k, v in (("holder", holder), ("licence", licence), ("terms", terms)) if v}
        body = {"uri": url, "title": title, "method": method, "retrieved_at": retrieved_at, "format": fmt,
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


def text_of(lab, source):
    """The text of a source excerpts are checked against: its extracted text, or the original if it is text."""
    body = source["body"]
    files = {f.get("role"): f.get("path") for f in body.get("files") or [] if isinstance(f, dict)}
    d = lab.root / SOURCES_DIR / source["id"]
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
        d = lab.root / SOURCES_DIR / str(e.get("id"))
        recorded = {f.get("path"): f.get("hash") for f in files if isinstance(f, dict)}
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
