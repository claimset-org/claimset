"""Revisions of published claimsets (revision spec v0.2): reviews, responses and revised versions.

A claimset's record is a folder in the layout of the platform's registry (revision spec §2.4):

    <concept id>/
      v1/claimset.json  v1/lab.jsonl  v1/reviews/RV1.json
      v2/claimset.json  v2/lab.jsonl  v2/response-RP1.json  v2/reviews/RV2.json  v2/resolutions/RL1.json

`Record` reads it and works out the state of every review comment (§6) and the latest scores the reviewer gave
(§7). The functions below check reviews and resolutions (R1-R7, R18), check a revision submission with its
response (R8-R17), draft a response and compute the patch the platform records (§5.3). Scores are given by the
platform's reviewer against the portal's rubric; this module only checks that each rests on comments. The
command line is `claimset revision ...`.
"""

import copy
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import validate as _validate
from .lab import decision as _decision
from .lab.core import canonical, sha256

CATEGORIES = ("prior_work", "novelty", "scope", "assumption", "test", "measurement", "missing_result", "reproducibility",
              "reasoning", "presentation", "validation")
SEVERITIES = ("could_reverse", "could_bias_magnitude", "reduces_precision", "reduces_transferability",
              "presentation")
STATES = ("open", "resolved", "withdrawn", "upheld", "limited", "disputed")
UNRESOLVED = ("open", "upheld", "limited", "disputed")
RESPONSES = ("changed", "rebutted", "accepted_as_limitation", "test_challenged", "declined")
KINDS = ("initial", "re_review", "additional")
NO_TEST = ("presentation", "validation")
FRAMEWORKS = _decision.FRAMEWORKS + ("documentary",)

COLLECTIONS = ("contributors", "models", "definitions", "claims", "evidence", "warrants", "limitations",
               "artefacts", "references")
# Fields the platform assigns: not part of what the author changes (claimset §2.2)
PLATFORM_PAPER = ("id", "version", "previous_version", "changes", "date", "status", "status_notice", "answers")
PLATFORM_CLAIM = ("verifiability", "verifiability_flags")

DOC_ID = {"review": re.compile(r"^RV([1-9]\d*)$"), "response": re.compile(r"^RP([1-9]\d*)$"),
          "resolution": re.compile(r"^RL([1-9]\d*)$")}
COMMENT_ID = re.compile(r"^(?P<version>\S+@v(?P<n>[1-9]\d*))/(?P<review>RV[1-9]\d*)\.(?P<local>c[1-9]\d*)$")
LAB_ID = re.compile(r"^(?:[A-Z]{2}\d+|AC\d+\.[A-Z]{2}\d+)$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class RevisionError(Exception):
    pass


def doc_hash(doc):
    """SHA-256 of a document's canonical JSON (revision spec §2.3)."""
    return sha256(canonical(doc))


def short(gid):
    """A comment's global ID without the claimset: `v1/RV1.c4`."""
    m = COMMENT_ID.match(str(gid))
    return f"v{m.group('n')}/{m.group('review')}.{m.group('local')}" if m else str(gid)


def _num(doc_id):
    m = re.match(r"^[A-Z]{2}(\d+)$", str(doc_id))
    return int(m.group(1)) if m else 0


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RevisionError(f"cannot read {path}: {exc}") from None


def _as_list(x):
    return x if isinstance(x, list) else []


def _as_dict(x):
    return x if isinstance(x, dict) else {}


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, rule, where, message):
        self.errors.append({"rule": rule, "where": where, "message": message})

    def warn(self, rule, where, message):
        self.warnings.append({"rule": rule, "where": where, "message": message})

    def as_dict(self):
        return {"ok": not self.errors, "errors": self.errors, "warnings": self.warnings}


# ---------------------------------------------------------------- the record

class Version:
    def __init__(self, n, folder):
        self.n = n
        self.folder = Path(folder)
        self.claimset = _load(self.folder / "claimset.json")
        self.hash = doc_hash(self.claimset)
        self.lab = self.folder / "lab.jsonl" if (self.folder / "lab.jsonl").is_file() else None
        self.reviews = [_load(p) for p in sorted((self.folder / "reviews").glob("RV*.json"), key=lambda p: _num(p.stem))]
        self.resolutions = [_load(p) for p in sorted((self.folder / "resolutions").glob("RL*.json"),
                                                     key=lambda p: _num(p.stem))]
        responses = sorted(self.folder.glob("response-RP*.json"))
        self.response = _load(responses[0]) if responses else None


class Comment:
    """A review comment with its life so far (§6)."""

    def __init__(self, gid, comment, review, version):
        self.id = gid
        self.comment = comment
        self.review = review            # the review's global ID
        self.version = version          # the number of the version it was raised on
        self.claims = [c for c in _as_list(comment.get("claims")) if isinstance(c, str)]
        self.test = comment.get("resolution_test")
        self.state = "open"
        self.verdicts = []              # (document global ID, verdict)
        self.disputed_rounds = 0        # consecutive re-reviews that left it disputed
        self.human_requested = False

    @property
    def unresolved(self):
        return self.state in UNRESOLVED

    def summary(self):
        return {"comment": self.id, "claims": self.claims, "category": self.comment.get("category"),
                "severity": self.comment.get("severity"), "state": self.state, "raised_on": self.version,
                "comment_text": self.comment.get("comment"), "requested_action": self.comment.get("requested_action"),
                "resolution_test": self.test}


class Record:
    """A claimset's record folder (revision spec §2.4)."""

    def __init__(self, folder):
        self.folder = Path(folder)
        if not self.folder.is_dir():
            raise RevisionError(f"{folder}: no such record folder")
        found = {}
        for d in self.folder.iterdir():
            m = re.match(r"^v([1-9]\d*)$", d.name)
            if d.is_dir() and m and (d / "claimset.json").is_file():
                found[int(m.group(1))] = d
        if not found:
            raise RevisionError(f"{folder}: no version folders (v1/claimset.json, …)")
        if sorted(found) != list(range(1, max(found) + 1)):
            raise RevisionError(f"{folder}: versions {sorted(found)} are not consecutive from 1")
        self.versions = {n: Version(n, d) for n, d in sorted(found.items())}
        self.concept = _as_dict(self.versions[1].claimset.get("paper")).get("id")
        if not self.concept:
            raise RevisionError(f"{folder}/v1/claimset.json is not a published claimset: it has no paper.id")

    @property
    def current(self):
        return self.versions[max(self.versions)]

    def vid(self, n):
        return f"{self.concept}@v{n}"

    def documents(self, upto=None, exclude=None):
        """(kind, version, document) in publication order: per version, its response (published with it), then
        its reviews and resolutions by date."""
        out = []
        for n, v in self.versions.items():
            if upto is not None and n > upto:
                break
            if v.response is not None:
                out.append(("response", n, v.response))
            later = [("review", n, r) for r in v.reviews] + [("resolution", n, r) for r in v.resolutions]
            later.sort(key=lambda t: (str(t[2].get("date", "")), 0 if t[0] == "review" else 1, _num(t[2].get("id"))))
            out += later
        return [t for t in out if exclude is None or t[2] != exclude]

    def comments(self, upto=None, exclude=None):
        """{global ID: Comment} with each comment's state after the documents published so far (up to version
        `upto`, leaving out the document `exclude`)."""
        out = {}
        for kind, n, d in self.documents(upto, exclude):
            gid = f"{self.vid(n)}/{d.get('id')}"
            if kind == "review":
                for c in _as_list(d.get("comments")):
                    if isinstance(c, dict) and c.get("id"):
                        cid = f"{gid}.{c['id']}"
                        out[cid] = Comment(cid, c, gid, n)
            if kind in ("review", "resolution"):
                for v in _as_list(d.get("verdicts")):
                    c = out.get(_as_dict(v).get("comment"))
                    if c is None:
                        continue
                    c.verdicts.append((gid, v))
                    if v.get("state") in STATES:
                        c.state = v["state"]
                    if isinstance(v.get("replacement_test"), dict):
                        c.test = v["replacement_test"]
                    if isinstance(v.get("accepted_test"), dict):
                        c.test = v["accepted_test"]
                    if kind == "review" and d.get("kind") == "re_review":
                        c.disputed_rounds = c.disputed_rounds + 1 if v.get("state") == "disputed" else 0
            if kind == "response":
                for e in _as_list(d.get("entries")):
                    c = out.get(_as_dict(e).get("comment"))
                    if c is not None and e.get("request_human"):
                        c.human_requested = True
        return out

    def unresolved(self, upto=None, exclude=None):
        return {k: c for k, c in self.comments(upto, exclude).items() if c.unresolved}

    def scores(self, n=None):
        """{claim: {score, comments, reason, review}}: the latest score given to each claim of version n (default:
        the current one), from the reviews of that version in publication order (§7.3)."""
        n = n or max(self.versions)
        out = {}
        for kind, vn, d in self.documents(upto=n):
            if kind != "review" or vn != n:
                continue
            for x in _as_list(d.get("scores")):
                x = _as_dict(x)
                if x.get("claim"):
                    out[x["claim"]] = dict(x, review=f"{self.vid(n)}/{d.get('id')}")
        return out

    def novelty(self, n=None):
        """{claim: {closest, searched, comments, review}}: the latest novelty assessment of each claim of version n
        (default: the current one) (§7.3)."""
        n = n or max(self.versions)
        out = {}
        for kind, vn, d in self.documents(upto=n):
            if kind != "review" or vn != n:
                continue
            for x in _as_list(d.get("novelty")):
                x = _as_dict(x)
                if x.get("claim"):
                    out[x["claim"]] = dict(x, review=f"{self.vid(n)}/{d.get('id')}")
        return out

    def removed_ids(self):
        """IDs that some version had and a later version dropped (R16)."""
        seen, removed = set(), set()
        for v in self.versions.values():
            ids = object_ids(v.claimset)
            removed |= seen - set(ids)
            seen |= set(ids)
        return removed


# ---------------------------------------------------------------- claimset objects and field paths

def object_ids(doc):
    """{object ID: (collection, object)} for every identified object of a claimset."""
    out = {}
    for coll in COLLECTIONS:
        for o in _as_list(doc.get(coll)):
            if isinstance(o, dict) and isinstance(o.get("id"), str):
                out[o["id"]] = (coll, o)
    return out


_MISSING = object()


def rests_on(doc, cid, seen=None):
    """Every claim a claim rests on, directly or through others: depends_on and assumptions, including the
    assumptions of the evidence it cites (§7.2)."""
    claims = {c.get("id"): c for c in _as_list(doc.get("claims")) if isinstance(c, dict)}
    evidence = {e.get("id"): e for e in _as_list(doc.get("evidence")) if isinstance(e, dict)}
    seen = set() if seen is None else seen
    c = claims.get(cid)
    if c is None:
        return seen
    direct = list(_as_list(c.get("depends_on"))) + list(_as_list(c.get("assumptions")))
    for sup in _as_list(c.get("support")):
        direct += list(_as_list(_as_dict(evidence.get(_as_dict(sup).get("evidence"))).get("assumptions")))
    for d in direct:
        if d in claims and d not in seen and d != cid:
            seen.add(d)
            rests_on(doc, d, seen)
    return seen


def resolve_path(doc, path):
    """The value at a field path (§2.2): `C1.statement`, `paper.title`; _MISSING if there is none."""
    parts = str(path).split(".")
    if parts[0] == "paper":
        cur = doc.get("paper")
    else:
        found = object_ids(doc).get(parts[0])
        cur = found[1] if found else _MISSING
    for p in parts[1:]:
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    return _MISSING if cur is None and parts[0] == "paper" and len(parts) == 1 else cur


def covers(path, field):
    """Whether field path `path` covers `field` (§2.2)."""
    return field == path or field.startswith(path + ".")


def _content(doc):
    """A claimset without the fields the platform assigns, as the author writes it."""
    d = copy.deepcopy(doc)
    paper = _as_dict(d.get("paper"))
    for f in PLATFORM_PAPER + ("correction_reason",):
        paper.pop(f, None)
    cls = paper.get("classification")
    if isinstance(cls, dict):
        cls.pop("subfield", None)
        cls.pop("topics", None)
        if not cls:
            paper.pop("classification", None)
    for c in _as_list(d.get("claims")):
        if isinstance(c, dict):
            for f in PLATFORM_CLAIM:
                c.pop(f, None)
    return d


def _ptr(*parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _diff_dict(a, b, ptr, field, out):
    for k in sorted(set(a) | set(b)):
        p, f = ptr + _ptr(k), f"{field}.{k}"
        if k not in b:
            out.append(({"op": "remove", "path": p}, f))
        elif k not in a:
            out.append(({"op": "add", "path": p, "value": b[k]}, f))
        elif a[k] != b[k]:
            if isinstance(a[k], dict) and isinstance(b[k], dict):
                _diff_dict(a[k], b[k], p, f, out)
            else:
                out.append(({"op": "replace", "path": p, "value": b[k]}, f))


def raw_patch(previous, revision):
    """[(operation, field path)]: the JSON Patch from one version's content to the next's, object by object
    (revision spec §5.3). Applied in order to the previous content, the operations give the revision's."""
    a, b = _content(previous), _content(revision)
    out = []
    for key in sorted(set(a) | set(b)):
        if key in COLLECTIONS and isinstance(a.get(key, []), list) and isinstance(b.get(key, []), list):
            pa, pb = a.get(key, []), b.get(key, [])
            ida = [o.get("id") if isinstance(o, dict) else None for o in pa]
            idb = [o.get("id") if isinstance(o, dict) else None for o in pb]
            kept_a = [i for i in ida if i in idb]
            kept_b = [i for i in idb if i in ida]
            if None in ida or None in idb or len(set(ida)) != len(ida) or len(set(idb)) != len(idb) or kept_a != kept_b:
                if pa != pb:
                    out.append(({"op": "replace", "path": _ptr(key), "value": pb}, key))
                continue
            if key not in a:
                out.append(({"op": "add", "path": _ptr(key), "value": []}, key))
            for i in sorted((i for i, x in enumerate(ida) if x not in idb), reverse=True):
                out.append(({"op": "remove", "path": _ptr(key, i)}, ida[i]))
            for j, oid in enumerate(kept_a):
                _diff_dict(pa[ida.index(oid)], pb[idb.index(oid)], _ptr(key, j), oid, out)
            for j, oid in enumerate(idb):
                if oid not in ida:
                    out.append(({"op": "add", "path": _ptr(key, j), "value": pb[j]}, oid))
            if key not in b:
                out.append(({"op": "remove", "path": _ptr(key)}, key))
        elif key == "paper" and isinstance(a.get(key), dict) and isinstance(b.get(key), dict):
            _diff_dict(a[key], b[key], "/paper", "paper", out)
        elif key not in b:
            out.append(({"op": "remove", "path": _ptr(key)}, key))
        elif key not in a:
            out.append(({"op": "add", "path": _ptr(key), "value": b[key]}, key))
        elif a[key] != b[key]:
            out.append(({"op": "replace", "path": _ptr(key), "value": b[key]}, key))
    return out


def apply_patch(doc, ops):
    """Apply JSON Patch operations (add, remove, replace) to a copy of doc; for checking a patch."""
    doc = copy.deepcopy(doc)
    for op in ops:
        parts = [p.replace("~1", "/").replace("~0", "~") for p in op["path"].split("/")[1:]]
        parent = doc
        for p in parts[:-1]:
            parent = parent[int(p)] if isinstance(parent, list) else parent[p]
        last = parts[-1]
        if isinstance(parent, list):
            i = int(last)
            if op["op"] == "add":
                parent.insert(i, copy.deepcopy(op["value"]))
            elif op["op"] == "remove":
                del parent[i]
            else:
                parent[i] = copy.deepcopy(op["value"])
        elif op["op"] == "remove":
            del parent[last]
        else:
            parent[last] = copy.deepcopy(op["value"])
    return doc


def patch(previous, revision, response):
    """The patch the platform records in `paper.changes` (revision spec §5.3): each operation with the reason
    and the comments of the response entries and other changes whose paths cover it."""
    out = []
    entries = [e for e in _as_list(_as_dict(response).get("entries")) if isinstance(e, dict)]
    others = [o for o in _as_list(_as_dict(response).get("other_changes")) if isinstance(o, dict)]
    for op, field in raw_patch(previous, revision):
        op = dict(op)
        addresses = [e["comment"] for e in entries
                     if any(covers(str(p), field) for p in _entry_paths(e)) and e.get("comment")]
        reasons = [str(o.get("reason")) for o in others if covers(str(o.get("path")), field) and o.get("reason")]
        if field == "schema_version":
            reasons = reasons or ["the claimset follows a later version of the specification"]
        if addresses:
            op["addresses"] = addresses
            reasons = reasons or [f"answers {', '.join(a.rsplit('/', 1)[-1] for a in addresses)}"]
        op["reason"] = "; ".join(reasons) if reasons else ""
        out.append((op, field))
    return out


def _entry_paths(entry):
    """The field paths a response entry answers with: its changes, and the limitation it adds."""
    out = [str(p) for p in _as_list(entry.get("changes"))]
    if entry.get("response") == "accepted_as_limitation" and isinstance(entry.get("limitation"), str):
        out.append(entry["limitation"])
    return out


def changed_fields(previous, revision):
    """Field paths that differ between two versions (objects added or removed, fields changed)."""
    return [f for _, f in raw_patch(previous, revision)]


# ---------------------------------------------------------------- tests and models

def test_problems(test):
    """Problems of a resolution test (R4): empty if it is complete."""
    if not isinstance(test, dict):
        return ["a resolution test {procedure, framework, parameters, quantities, upheld, withdrawn} is required"]
    out = []
    if not isinstance(test.get("procedure"), str) or not test["procedure"].strip():
        out.append("the test needs a procedure: what to run or check, on what")
    fw = test.get("framework")
    if fw not in FRAMEWORKS:
        return out + [f"framework must be one of {', '.join(FRAMEWORKS)}, not {fw!r}"]
    if fw == "documentary":
        if test.get("quantities"):
            out.append("a documentary test is decided by what a source states; it has no quantities")
        for rule in ("upheld", "withdrawn"):
            if not isinstance(test.get(rule), str) or not test[rule].strip():
                out.append(f"the '{rule}' rule must say what the source would have to state")
        return out
    as_rule = {"framework": fw, "parameters": test.get("parameters", {}), "supported": test.get("withdrawn"),
               "not_supported": test.get("upheld")}
    if test.get("quantities") is not None:
        as_rule["quantities"] = test["quantities"]
    for p in _decision.problems(as_rule):
        out.append(p.replace("'supported' rule", "'withdrawn' rule").replace("'not_supported' rule", "'upheld' rule")
                   .replace("the 'supported'", "the 'withdrawn'").replace("the 'not_supported'", "the 'upheld'"))
    return out


def computable(test):
    return isinstance(test, dict) and test.get("framework") != "documentary" and bool(test.get("quantities"))


def _model_keys(models):
    out = set()
    for m in models:
        m = _as_dict(m)
        if m.get("identifier"):
            out.add(("id", str(m["identifier"]).lower()))
        if m.get("name"):
            out.add(("nv", str(m.get("provider", "")).lower(), str(m["name"]).lower(), str(m.get("version", "")).lower()))
    return out


def author_models(doc):
    by_id = {m.get("id"): m for m in _as_list(doc.get("models")) if isinstance(m, dict)}
    out = []
    for p in _as_list(doc.get("contributors")):
        p = _as_dict(p)
        if p.get("type") == "agent" and "author" in _as_list(p.get("positions")):
            for ref in _as_list(p.get("models")):
                m = by_id.get(_as_dict(ref).get("model"))
                if m:
                    out.append(m)
    return out


# ---------------------------------------------------------------- reviews and resolutions (R1-R7)

def _check_verdict(r, w, v, comments, is_resolution):
    v = _as_dict(v)
    c = comments.get(v.get("comment"))
    if c is None:
        r.err("R5" if not is_resolution else "R7", w, f"'{v.get('comment')}' is not a comment of this claimset's "
                                                      f"reviews")
        return
    st = v.get("state")
    allowed = ("withdrawn", "upheld") if is_resolution else STATES
    if st not in allowed:
        r.err("R7", f"{w}.state", f"must be one of {', '.join(allowed)}")
        return
    if not isinstance(v.get("reason"), str) or not v["reason"].strip():
        r.err("R7", f"{w}.reason", "a verdict says why, in a self-contained sentence")
    if st in ("withdrawn", "upheld"):
        met = _as_dict(v.get("rule")).get("met")
        if met != st:
            r.err("R7", f"{w}.rule", f"a verdict of '{st}' names the rule of the test that the evidence met: "
                                     f"{{\"met\": \"{st}\", …}}")
        if not _as_list(v.get("evidence")):
            r.err("R7", f"{w}.evidence", f"a verdict of '{st}' cites the evidence it rests on")
    if st == "disputed":
        rt = v.get("replacement_test")
        probs = test_problems(rt)
        if probs:
            r.err("R7", f"{w}.replacement_test", "a disputed comment gets a replacement test: " + "; ".join(probs))
        elif rt == c.test:
            r.err("R7", f"{w}.replacement_test", "the replacement test must differ from the test it replaces")
    if v.get("accepted_test") is not None and test_problems(v["accepted_test"]):
        r.err("R7", f"{w}.accepted_test", "; ".join(test_problems(v["accepted_test"])))


def check_review(record, doc):
    """Check a review or a resolution against the record (R1-R7, R18, R19, W-R1). `doc` may already be in the record."""
    r = Report()
    doc = _as_dict(doc)
    schema = doc.get("schema")
    is_resolution = schema == "claimset-resolution"
    if schema not in ("claimset-review", "claimset-resolution"):
        r.err("R1", "schema", "must be 'claimset-review' or 'claimset-resolution'")
        return r
    rid = doc.get("id")
    if not DOC_ID["resolution" if is_resolution else "review"].match(str(rid)):
        r.err("R1", "id", f"must be {'RL' if is_resolution else 'RV'}<n>")
    target = _as_dict(doc.get("target"))
    m = re.match(r"^(.*)@v([1-9]\d*)$", str(target.get("version", "")))
    if not m or m.group(1) != record.concept or int(m.group(2)) not in record.versions:
        r.err("R1", "target.version", f"must be a published version of {record.concept} (…@v<n>)")
        return r
    n = int(m.group(2))
    version = record.versions[n]
    if target.get("hash") != version.hash:
        r.err("R1", "target.hash", f"must be the hash of the published file of v{n}, {version.hash}")
    others = [d for k, vn, d in record.documents(exclude=doc) if k in ("review", "resolution")]
    if any(d.get("id") == rid and d.get("schema") == schema for d in others):
        r.err("R1", "id", f"{rid} is already used in this claimset's record")
    kind = doc.get("kind")
    if is_resolution:
        if n != max(record.versions):
            r.err("R1", "target.version", "a resolution is attached to the current version")
    elif kind not in KINDS:
        r.err("R1", "kind", f"must be one of {', '.join(KINDS)}")
    elif kind == "initial" and (n != 1 or any(d.get("kind") == "initial" for d in others if "kind" in d)):
        r.err("R1", "kind", "the initial review targets version 1, once")
    elif kind == "re_review" and (n == 1 or any(d.get("kind") == "re_review" and
                                                _as_dict(d.get("target")).get("version") == target.get("version")
                                                for d in others)):
        r.err("R1", "kind", "a re-review targets a revision (version 2 or later), once per revision")
    elif kind == "additional" and n != max(record.versions):
        r.err("R1", "kind", "an additional review targets the current version")

    # R2: who wrote it
    who = _as_dict(doc.get("resolver" if is_resolution else "reviewer"))
    where = "resolver" if is_resolution else "reviewer"
    if is_resolution or who.get("type") == "human":
        for f in ("name", "orcid", "competing_interests"):
            if not who.get(f):
                r.err("R2", f"{where}.{f}", "required for a human reviewer or resolver")
        if is_resolution:
            orcids = {_as_dict(p).get("orcid") for p in _as_list(version.claimset.get("contributors"))} - {None}
            if who.get("orcid") in orcids:
                r.err("R2", "resolver.orcid", "a resolver is not a contributor, author, supervisor or guarantor of "
                                              "the claimset")
    elif who.get("type") == "agent":
        if not _as_list(who.get("models")):
            r.err("R2", "reviewer.models", "an agent reviewer lists its models")
        prompt = _as_dict(who.get("prompt"))
        for f in ("version", "uri", "hash"):
            if not prompt.get(f):
                r.err("R2", f"reviewer.prompt.{f}", "an agent reviewer's prompt is public: version, uri and hash")
        if _as_list(doc.get("scores")):
            rubric = _as_dict(who.get("rubric"))
            for f in ("version", "uri", "hash"):
                if not rubric.get(f):
                    r.err("R2", f"reviewer.rubric.{f}", "a review that scores names the public rubric it applied: "
                                                         "version, uri and hash")
        if _model_keys(who.get("models") or []) & _model_keys(author_models(version.claimset)):
            r.warn("W-R1", "reviewer.models", "the reviewer runs on the same model as an author agent of the "
                                              "claimset")
    else:
        r.err("R2", "reviewer.type", "must be 'agent' or 'human'")

    claims = {c.get("id") for c in _as_list(version.claimset.get("claims")) if isinstance(c, dict)}
    # R3, R4, R6: comments
    changed = None
    if kind == "re_review" and n > 1:
        changed = changed_fields(record.versions[n - 1].claimset, version.claimset)
    seen = set()
    for i, c in enumerate(_as_list(doc.get("comments")) if not is_resolution else []):
        w = f"comments[{i}]"
        c = _as_dict(c)
        if not re.match(r"^c[1-9]\d*$", str(c.get("id"))) or c.get("id") in seen:
            r.err("R3", f"{w}.id", "c<n>, unique in the review")
        seen.add(c.get("id"))
        cl = _as_list(c.get("claims"))
        if not cl:
            r.err("R3", f"{w}.claims", "a comment names at least one claim it affects")
        for cid in cl:
            if cid not in claims:
                r.err("R3", f"{w}.claims", f"'{cid}' is not a claim of v{n}")
        for t in _as_list(c.get("targets")):
            if resolve_path(version.claimset, t) is _MISSING:
                r.err("R3", f"{w}.targets", f"'{t}' is not an object or field of v{n}")
        if c.get("category") not in CATEGORIES:
            r.err("R3", f"{w}.category", f"must be one of {', '.join(CATEGORIES)}")
        if c.get("category") == "novelty":
            if "severity" in c:
                r.err("R3", f"{w}.severity", "a novelty comment has no severity: it questions a label, not whether "
                                             "the claim holds")
            if not _as_list(c.get("references")):
                r.err("R3", f"{w}.references", "a novelty comment cites the earlier work that questions the label")
        elif c.get("severity") not in SEVERITIES:
            r.err("R3", f"{w}.severity", f"must be one of {', '.join(SEVERITIES)}")
        for f in ("comment", "requested_action"):
            if not isinstance(c.get(f), str) or not c[f].strip():
                r.err("R3", f"{w}.{f}", "required, self-contained text")
        if c.get("category") not in NO_TEST:
            probs = test_problems(c.get("resolution_test"))
            if probs:
                r.err("R4", f"{w}.resolution_test", "; ".join(probs))
        if changed is not None and c.get("severity") != "could_reverse":
            named = [str(x) for x in cl + _as_list(c.get("targets"))]
            if not any(covers(p, f) or covers(f, p) for p in named for f in changed):
                r.err("R6", w, "a new comment in a re-review is on what the revision changed, or could reverse a "
                               "claim; everything else belonged in an earlier review")
    # R5, R7: verdicts
    if is_resolution:
        comments = record.comments(exclude=doc)
    else:
        comments = record.comments(upto=n - 1) if kind == "re_review" else record.comments(exclude=doc)
    for i, v in enumerate(_as_list(doc.get("verdicts"))):
        _check_verdict(r, f"verdicts[{i}]", v, comments, is_resolution)
        c = comments.get(_as_dict(v).get("comment"))
        if c is not None and not c.unresolved:
            r.err("R5", f"verdicts[{i}]", f"{c.id} is {c.state}; verdicts are given on unresolved comments")
    if kind == "re_review" and not is_resolution:
        due = {k for k, c in comments.items() if c.unresolved and c.version < n}
        given = {_as_dict(v).get("comment") for v in _as_list(doc.get("verdicts"))}
        for k in sorted(due - given):
            r.err("R5", "verdicts", f"no verdict on {k}, unresolved on v{n - 1}")
    elif _as_list(doc.get("verdicts")) and not is_resolution:
        r.err("R5", "verdicts", "only a re-review gives verdicts on earlier comments")
    after = _states_after(record, doc, n, comments)
    _check_scores(r, record, doc, n, after, is_resolution, claims)
    _check_novelty(r, record, doc, n, after, is_resolution)
    return r


def _states_after(record, doc, n, before):
    """{comment global ID: (state, claims, category)} once this review is published: earlier comments after its
    verdicts, and its own comments, open."""
    state = {k: (c.state, c.claims, c.comment.get("category")) for k, c in before.items()}
    for v in _as_list(doc.get("verdicts")):
        v = _as_dict(v)
        if v.get("comment") in state and v.get("state") in STATES:
            old = state[v["comment"]]
            state[v["comment"]] = (v["state"], old[1], old[2])
    for c in _as_list(doc.get("comments")):
        c = _as_dict(c)
        if c.get("id"):
            gid = f"{record.vid(n)}/{doc.get('id')}.{c['id']}"
            state[gid] = ("open", [x for x in _as_list(c.get("claims")) if isinstance(x, str)], c.get("category"))
    return state


def _own(record, doc, n, category=None):
    """{global ID: claims} of the review's own comments (of one category)."""
    out = {}
    for c in _as_list(doc.get("comments")):
        c = _as_dict(c)
        if c.get("id") and (category is None or c.get("category") == category):
            out[f"{record.vid(n)}/{doc.get('id')}.{c['id']}"] = [x for x in _as_list(c.get("claims"))
                                                                 if isinstance(x, str)]
    return out


def _check_novelty(r, record, doc, n, state, is_resolution):
    """R19: the novelty assessment of every claim, with the search it rests on (§7.3)."""
    entries = doc.get("novelty")
    kind = doc.get("kind")
    human = is_resolution or _as_dict(doc.get("reviewer")).get("type") == "human"
    target = record.versions[n].claimset
    claims = {c.get("id"): c for c in _as_list(target.get("claims")) if isinstance(c, dict)}
    due = {cid for cid, c in claims.items() if not _validate.is_assumption(c)}
    if not entries:
        if kind in ("initial", "re_review") and not human and due:
            r.err("R19", "novelty", "an initial review or a re-review assesses the novelty of every claim that is "
                                    "not an assumption")
        return
    if human:
        r.err("R19", "novelty", "only the platform's reviewer assesses novelty; human reviews and resolutions "
                                "raise novelty comments instead")
        return
    if not isinstance(entries, list):
        r.err("R19", "novelty", "a list of {claim, closest, searched, comments}")
        return
    named = {x for cl in _own(record, doc, n, "novelty").values() for x in cl}
    seen = set()
    for i, x in enumerate(entries):
        w = f"novelty[{i}]"
        x = _as_dict(x)
        cid = x.get("claim")
        if cid not in due:
            r.err("R19", f"{w}.claim", f"'{cid}' is not a claim of v{n} that has a novelty (assumptions have none)")
            continue
        if cid in seen:
            r.err("R19", f"{w}.claim", f"{cid} is assessed twice")
        seen.add(cid)
        if kind == "additional" and cid not in named:
            r.err("R19", f"{w}.claim", f"an additional review assesses only the claims its novelty comments name")
        searched = _as_dict(x.get("searched"))
        sources = _as_list(searched.get("sources"))
        if not sources or not all(isinstance(t, str) and t.strip() for t in sources):
            r.err("R19", f"{w}.searched.sources", "say where the search looked: the platform's claims, literature "
                                                  "databases, the claimset's references")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(searched.get("date", ""))):
            r.err("R19", f"{w}.searched.date", "the date of the search, YYYY-MM-DD")
        for j, ref in enumerate(_as_list(x.get("closest"))):
            if not (isinstance(ref, dict) and ref.get("target") and ref.get("id_type")):
                r.err("R19", f"{w}.closest[{j}]", "a reference with target and id_type, and a note on the difference")
        for k in _as_list(x.get("comments")):
            st = state.get(k)
            if st is None:
                r.err("R19", f"{w}.comments", f"'{k}' is not a comment of this claimset")
            elif st[2] != "novelty":
                r.err("R19", f"{w}.comments", f"{short(k)} is not a novelty comment")
            elif st[0] not in UNRESOLVED:
                r.err("R19", f"{w}.comments", f"{short(k)} is {st[0]}: it no longer questions the label")
            elif cid not in st[1]:
                r.err("R19", f"{w}.comments", f"{short(k)} does not name {cid}")
    if kind in ("initial", "re_review"):
        for cid in sorted(due - seen):
            r.err("R19", "novelty", f"no novelty assessment of {cid}")


def _check_scores(r, record, doc, n, before, is_resolution, claims):
    # `before`: the state of every comment once this review is published (_states_after)
    """R18: who scores which claims, and every point below 100 rests on unresolved comments on the claim or on
    what it rests on (§7)."""
    scores = doc.get("scores")
    kind = doc.get("kind")
    human = is_resolution or _as_dict(doc.get("reviewer")).get("type") == "human"
    if scores is None or scores == []:
        if kind in ("initial", "re_review") and not is_resolution:
            r.err("R18", "scores", "an initial review or a re-review scores every claim of the version")
        return
    if human:
        r.err("R18", "scores", "only the platform's reviewer scores; human reviews and resolutions do not")
        return
    if not isinstance(scores, list):
        r.err("R18", "scores", "a list of {claim, score, comments, reason}")
        return
    state = before
    own = _own(record, doc, n)
    target = record.versions[n].claimset
    named_by_own = {x for cl in own.values() for x in cl}
    seen = set()
    for i, x in enumerate(scores):
        w = f"scores[{i}]"
        x = _as_dict(x)
        cid = x.get("claim")
        if cid not in claims:
            r.err("R18", f"{w}.claim", f"'{cid}' is not a claim of v{n}")
            continue
        if cid in seen:
            r.err("R18", f"{w}.claim", f"{cid} is scored twice")
        seen.add(cid)
        if kind == "additional" and cid not in named_by_own:
            r.err("R18", f"{w}.claim", f"an additional review scores only the claims its comments name; none "
                                       f"names {cid}")
        sc = x.get("score")
        if not isinstance(sc, int) or isinstance(sc, bool) or not 0 <= sc <= 100:
            r.err("R18", f"{w}.score", "an integer from 0 to 100")
            continue
        cited = _as_list(x.get("comments"))
        if sc == 100 and cited:
            r.err("R18", f"{w}.comments", "a score of 100 rests on no comment")
        if sc < 100:
            if not cited:
                r.err("R18", f"{w}.comments", "every point below 100 rests on a comment the author can answer: "
                                              "cite the comments (global IDs)")
            if not isinstance(x.get("reason"), str) or not x["reason"].strip():
                r.err("R18", f"{w}.reason", "say how the cited comments bear on the claim")
        reach = {cid} | rests_on(target, cid)
        for k in cited:
            st = state.get(k)
            if st is None:
                r.err("R18", f"{w}.comments", f"'{k}' is not a comment of this claimset")
            elif st[2] == "novelty":
                r.err("R18", f"{w}.comments", f"{short(k)} questions novelty, not credibility: it does not lower a "
                                              f"score")
            elif st[0] not in UNRESOLVED:
                r.err("R18", f"{w}.comments", f"{short(k)} is {st[0]}: a settled comment no longer lowers a score")
            elif not set(st[1]) & reach:
                r.err("R18", f"{w}.comments", f"{short(k)} names neither {cid} nor a claim it rests on")
    if kind in ("initial", "re_review"):
        for cid in sorted(set(claims) - seen - {None}):
            r.err("R18", "scores", f"no score for {cid}: an initial review or a re-review scores every claim")


# ---------------------------------------------------------------- lab records

class LabFile:
    """A lab record published with a version: its lines, and the events appended after a previous record."""

    def __init__(self, path, previous=None):
        self.path = Path(path)
        self.lines = self.path.read_text(encoding="utf-8").splitlines()
        prev = Path(previous).read_text(encoding="utf-8").splitlines() if previous else []
        self.prefix_ok = self.lines[:len(prev)] == prev
        self.events = []
        for line in self.lines:
            try:
                self.events.append(json.loads(line))
            except json.JSONDecodeError:
                self.events.append({})
        self.appended = {e.get("id") for e in self.events[len(prev):] if isinstance(e, dict)}
        from .lab.core import State
        self.state = State()
        for e in self.events:
            try:
                self.state.apply(e)
            except Exception:       # a record that does not verify is reported by R17
                pass

    def verify(self):
        from .lab.core import Lab, verify
        tmp = Path(tempfile.mkdtemp(prefix="claimset-lab-"))
        try:
            shutil.copyfile(self.path, tmp / "lab.jsonl")
            return verify(Lab(tmp), check_files=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def plans_for(self, comment_id):
        """Decided confirmatory plans that address a comment: [(plan root, version, deciding runs, outcome)]."""
        st, out = self.state, []
        for root in st.plan_roots:
            pv = st.latest(root)
            if pv is None or comment_id not in _as_list(_as_dict(pv.get("body")).get("addresses")):
                continue
            d = st.deciding_runs(root)
            if not d:
                out.append((root, pv, [], None))
                continue
            outcome = None
            for e in st.events:
                b = _as_dict(e.get("body"))
                if e.get("type") == "status" and b.get("plan") and st.root(b["plan"]) == root:
                    outcome = b.get("to")
            out.append((root, d[0], d[1], outcome))
        return out

    def run_of(self, event_id):
        e = self.state.get(event_id)
        if e is None:
            return None
        if e.get("type") == "run":
            return e
        if e.get("type") == "result":
            return self.state.get(_as_dict(e.get("body")).get("run"))
        return None


# ---------------------------------------------------------------- responses and revisions (R8-R17)

def check_revision(record, revision, response, lab=None):
    """Check a revision submission: the revised claimset, its response and lab record (R8-R17, W-R2-W-R4).
    `lab`: path of the revision's lab.jsonl, if it has one. Returns (report, patch)."""
    r = Report()
    cur = record.current
    prev_doc = cur.claimset
    vr = _validate.validate(revision, revision=True)
    for e in vr.errors:
        r.err(f"claimset {e['rule']}", e["where"], e["message"])
    paper = _as_dict(revision.get("paper"))
    if paper.get("id") != record.concept:
        r.err("R14", "paper.id", f"a revision names the claimset it revises: {record.concept}")
    if "correction_reason" in paper:
        r.err("R14", "paper.correction_reason", "a revision is answered with a response, not a correction reason")
    response = _as_dict(response)
    if response.get("schema") != "claimset-response":
        r.err("R8", "schema", "must be 'claimset-response'")
    if not DOC_ID["response"].match(str(response.get("id"))):
        r.err("R8", "id", "must be RP<n>")
    elif any(v.response and v.response.get("id") == response.get("id") for v in record.versions.values()):
        r.err("R8", "id", f"{response.get('id')} is already used in this claimset's record")
    rev = _as_dict(response.get("revises"))
    if rev.get("version") != record.vid(cur.n):
        r.err("R14", "revises.version", f"a revision revises the current version, {record.vid(cur.n)}")
    if rev.get("hash") != cur.hash:
        r.err("R8", "revises.hash", f"must be the hash of the published file of v{cur.n}, {cur.hash}")

    comments = record.comments()
    due = {k: c for k, c in comments.items() if c.unresolved}
    reviews = {f"{record.vid(n)}/{d.get('id')}": d for k, n, d in record.documents() if k == "review"}
    named = {}
    for i, x in enumerate(_as_list(response.get("responds_to"))):
        x = _as_dict(x)
        d = reviews.get(x.get("review"))
        if d is None:
            r.err("R8", f"responds_to[{i}]", f"'{x.get('review')}' is not a review of this claimset")
        elif x.get("hash") != doc_hash(d):
            r.err("R8", f"responds_to[{i}].hash", f"must be the hash of {x.get('review')}, {doc_hash(d)}")
        named[x.get("review")] = x
    for rv in sorted({c.review for c in due.values()} - set(named)):
        r.err("R8", "responds_to", f"must name {rv}, which has unresolved comments")

    lab_file = None
    if lab is not None:
        lab_file = LabFile(lab, cur.lab)
    # R17
    if cur.lab is not None and lab is None:
        r.err("R17", "lab", f"v{cur.n} has a lab record; the revision brings it, with the new events appended")
    if lab_file is not None:
        if not lab_file.prefix_ok:
            r.err("R17", "lab", f"the lab record does not begin with v{cur.n}'s record unchanged")
        rep = lab_file.verify()
        for e in rep.get("errors", [])[:10]:
            r.err("R17", "lab", f"the lab record does not verify: [{e.get('rule')}] {e.get('message')}")

    ids_rev = object_ids(revision)
    ids_prev = object_ids(prev_doc)
    entries = [e for e in _as_list(response.get("entries")) if isinstance(e, dict)]
    answered = {}
    for i, e in enumerate(entries):
        w = f"entries[{i}]"
        cid = e.get("comment")
        c = due.get(cid)
        if c is None:
            r.err("R8", f"{w}.comment", f"'{cid}' is not an unresolved comment of this claimset"
                  + (f" (it is {comments[cid].state})" if cid in comments else ""))
        elif cid in answered:
            r.err("R8", f"{w}.comment", f"{cid} is answered twice")
        answered[cid] = e
        kind = e.get("response")
        if kind not in RESPONSES:
            r.err("R8", f"{w}.response", f"must be one of {', '.join(RESPONSES)}")
            continue
        for j, p in enumerate(_as_list(e.get("changes"))):
            here = resolve_path(revision, p)
            if here is _MISSING:
                if not (str(p) in ids_prev and str(p) not in ids_rev):
                    r.err("R9", f"{w}.changes[{j}]", f"'{p}' is not in the revision (nor an object it removes)")
            elif resolve_path(prev_doc, p) == here:
                r.err("R9", f"{w}.changes[{j}]", f"'{p}' is the same as in v{cur.n}")
        if kind == "changed" and not _as_list(e.get("changes")):
            r.err("R9", f"{w}.changes", "a 'changed' answer lists the paths of the revision that meet the comment")
        if kind == "rebutted":
            ev = _as_list(e.get("evidence"))
            if not ev:
                r.err("R10", f"{w}.evidence", "a rebuttal cites evidence: an evidence object or reference of the "
                                              "revision, or a lab event appended since the previous version")
            for j, x in enumerate(ev):
                coll = ids_rev.get(x, (None,))[0]
                if coll in ("evidence", "references"):
                    continue
                if LAB_ID.match(str(x)):
                    if lab_file is None:
                        r.err("R10", f"{w}.evidence[{j}]", f"'{x}' looks like a lab event: give the revision's "
                                                           f"lab record (--lab)")
                    elif x not in lab_file.appended:
                        r.err("R10", f"{w}.evidence[{j}]", f"'{x}' is not a lab event appended since v{cur.n}")
                    continue
                r.err("R10", f"{w}.evidence[{j}]", f"'{x}' is not an evidence object or reference of the revision, "
                                                   f"nor a lab event")
            if c is not None and computable(c.test) and not _strong_rebuttal(cid, ev, revision, lab_file):
                r.warn("W-R2", w, f"the rebuttal of {cid} does not cite a confirmatory plan that ran its resolution "
                                  f"test: run it as a planned test addressing the comment (lab plan --comment)")
        if kind == "accepted_as_limitation" and c is not None:
            lid = e.get("limitation")
            lim = ids_rev.get(lid, (None, {}))
            ok = lim[0] == "limitations" and lid not in ids_prev and \
                set(_as_list(lim[1].get("affects"))) & set(c.claims)
            scoped = any(str(p).split(".")[0] in c.claims and ".scope" in str(p) for p in _as_list(e.get("changes")))
            if not ok and not scoped:
                r.err("R11", w, "name a limitation new in the revision that affects the comment's claims, or a "
                                "narrowed scope of one of them in changes")
        if kind == "test_challenged":
            probs = test_problems(e.get("alternative_test"))
            if probs:
                r.err("R12", f"{w}.alternative_test", "; ".join(probs))
            if not isinstance(e.get("reason"), str) or not e["reason"].strip():
                r.err("R12", f"{w}.reason", "say why the test is unfair, cannot be run or does not decide the comment")
        if e.get("request_human"):
            if kind not in ("rebutted", "test_challenged") or c is None or c.state != "disputed":
                r.err("R13", f"{w}.request_human", "a human resolver is requested only for a disputed comment, "
                                                    "with a rebuttal or a challenged test")
        if kind == "declined" and c is not None and c.comment.get("severity") == "could_reverse":
            r.warn("W-R3", w, f"{cid} could reverse {', '.join(c.claims)} and is declined")
    for k in sorted(set(due) - set(answered)):
        r.err("R8", "entries", f"no entry for {k} ({due[k].state})")
    for k, c in due.items():
        if c.state == "disputed" and c.disputed_rounds >= 2 and not c.human_requested and \
                not _as_dict(answered.get(k)).get("request_human"):
            r.warn("W-R4", k, "disputed in two consecutive re-reviews and no human resolution requested")

    # R16
    for oid in sorted(record.removed_ids() & set(ids_rev)):
        r.err("R16", oid, "this ID was removed in an earlier version and is never used again")
    for oid in sorted(set(ids_rev) & set(ids_prev)):
        if ids_rev[oid][0] != ids_prev[oid][0]:
            r.err("R16", oid, f"was a {ids_prev[oid][0][:-1]} object in v{cur.n}; an ID keeps its object")
    # R15
    ops = patch(prev_doc, revision, response)
    paths = [p for e in entries for p in _entry_paths(e)] + \
            [str(_as_dict(o).get("path")) for o in _as_list(response.get("other_changes"))]
    for i, o in enumerate(_as_list(response.get("other_changes"))):
        if not _as_dict(o).get("path") or not _as_dict(o).get("reason"):
            r.err("R15", f"other_changes[{i}]", "each other change has a path and a reason")
    for op, field in ops:
        if field != "schema_version" and not any(covers(p, field) for p in paths):
            r.err("R15", field, f"changed ({op['op']} {op['path']}) but no response entry or other change covers it")
    return r, [op for op, _ in ops]


def _strong_rebuttal(cid, evidence, revision, lab_file):
    """Whether a rebuttal cites the work of a confirmatory plan that ran the comment's test (W-R2)."""
    for c in _as_list(revision.get("claims")):
        test = _as_dict(_as_dict(c).get("test"))
        if cid in _as_list(test.get("addresses")):
            cited = {_as_dict(s).get("evidence") for s in _as_list(c.get("support"))}
            if cited & set(evidence):
                return True
    if lab_file is not None:
        runs = {r["id"] for _, _, rs, _ in lab_file.plans_for(cid) for r in rs}
        for x in evidence:
            run = lab_file.run_of(x)
            if run is not None and run.get("id") in runs:
                return True
        for c in _as_list(revision.get("evidence")):
            if _as_dict(c).get("id") in evidence and set(_as_list(c.get("lab_runs"))) & runs:
                return True
    return False


# ---------------------------------------------------------------- drafting a response

def respond(record, revision, lab=None):
    """A draft response to every unresolved comment, and what the author still has to decide.

    Returns (response, todo). Entries are `rebutted` when a plan in the revision's lab record addresses the
    comment and its verdict met the comment's withdrawn rule, `changed` when the revision changes what the comment
    is about, and `declined` otherwise; every entry is the author's to confirm."""
    cur = record.current
    lab_file = LabFile(lab, cur.lab) if lab is not None else None
    due = record.unresolved()
    changed = changed_fields(cur.claimset, revision)
    used = set()
    numbers = [_num(v.response.get("id")) for v in record.versions.values() if v.response]
    reviews = {f"{record.vid(n)}/{d.get('id')}": d for k, n, d in record.documents() if k == "review"}
    response = {"schema": "claimset-response", "schema_version": "0.1", "id": f"RP{max(numbers or [0]) + 1}",
                "revises": {"version": record.vid(cur.n), "hash": cur.hash},
                "responds_to": [{"review": rv, "hash": doc_hash(reviews[rv])}
                                for rv in sorted({c.review for c in due.values()}, key=lambda x: _num(x.rsplit("/", 1)[1]))],
                "entries": [], "other_changes": []}
    todo = []
    for cid, c in sorted(due.items(), key=lambda kv: (kv[1].version, _num(kv[1].review.rsplit("/", 1)[1]),
                                                       int(kv[0].rsplit(".c", 1)[1]))):
        name = short(cid)
        about = [str(x) for x in _as_list(c.comment.get("targets"))] or c.claims
        mine = [f for f in changed if any(covers(p, f) or covers(f, p) for p in about)]
        plans = lab_file.plans_for(cid) if lab_file is not None else []
        decided = [(root, pv, runs, outcome) for root, pv, runs, outcome in plans if runs and root in lab_file.appended]
        entry = {"comment": cid}
        if decided and decided[-1][3] == "supported":
            root, pv, runs, outcome = decided[-1]
            results = [x["id"] for rn in runs for x in lab_file.state.results_of.get(rn["id"], [])]
            evidence = [e.get("id") for e in _as_list(revision.get("evidence"))
                        if set(_as_list(_as_dict(e).get("lab_runs"))) & {rn["id"] for rn in runs}]
            entry.update(response="rebutted", evidence=evidence + [rn["id"] for rn in runs] + results)
            if mine:
                entry["changes"] = mine
            entry["argument"] = (f"Plan {root} ran the comment's resolution test as a planned test; its verdict met "
                                 f"the test's withdrawn rule.")
            todo.append(f"{name}: rebutted with plan {root}; check the evidence and say in 'argument' what the "
                        f"test found")
        elif decided:
            root, _, _, outcome = decided[-1]
            entry.update(response="declined")
            todo.append(f"{name}: plan {root} ran its resolution test and gave '{outcome}', not the withdrawn "
                        f"rule: change the claim ('changed'), state the problem ('accepted_as_limitation'), "
                        f"or say why not")
        elif mine:
            entry.update(response="changed", changes=mine)
            todo.append(f"{name}: 'changed' with {', '.join(mine)}; keep only the changes that meet the comment")
        else:
            entry.update(response="declined")
            todo.append(f"{name} ({c.comment.get('severity')}, {c.state}): not answered yet: change the "
                        f"claimset, rebut it with evidence (its test: {_test_line(c.test)}), accept it as a "
                        f"limitation, challenge the test, or decline it")
        used.update(_entry_paths(entry))
        response["entries"].append(entry)
    for f in changed:
        if f != "schema_version" and not any(covers(p, f) for p in used):
            response["other_changes"].append({"path": f, "reason": ""})
            todo.append(f"other_changes {f}: give the reason for this change, or list it under the comment it answers")
    return response, todo


def _test_line(test):
    t = _as_dict(test)
    if not t:
        return "none"
    return f"withdrawn if {t.get('withdrawn')}; upheld if {t.get('upheld')}"


# ---------------------------------------------------------------- command line

def _print_report(rep, as_json, extra=None):
    if as_json:
        out = rep.as_dict()
        if extra:
            out.update(extra)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    for e in rep.errors:
        print(f"ERROR [{e['rule']}] {e['where']}: {e['message']}")
    for w in rep.warnings:
        print(f"WARNING [{w['rule']}] {w['where']}: {w['message']}")
    print("OK" if not rep.errors else f"{len(rep.errors)} errors", "-", f"{len(rep.warnings)} warnings")


def cmd_status(args):
    rec = Record(args.record)
    comments = rec.comments()
    due = [c for c in comments.values() if c.unresolved]
    if args.json:
        print(json.dumps({"concept": rec.concept, "current": rec.vid(rec.current.n), "hash": rec.current.hash,
                          "scores": rec.scores(), "novelty": rec.novelty(),
                          "comments": [c.summary() for c in comments.values()],
                          "to_answer": [c.id for c in due]}, ensure_ascii=False, indent=2))
        return 0
    print(f"{rec.concept}: current version v{rec.current.n} ({rec.current.hash[:19]}…), "
          f"{len(comments)} comments, {len(due)} unresolved")
    scores = rec.scores()
    if scores:
        print("Scores (latest given by the reviewer):")
        for cid, x in scores.items():
            why = f" - {', '.join(short(k) for k in _as_list(x.get('comments')))}: {x.get('reason')}" \
                if x.get("score", 100) < 100 else ""
            print(f"  {cid}: {x.get('score')}{why}")
    else:
        print("No scores yet: the version has not been reviewed.")
    labels = {c.get("id"): c.get("novelty") for c in _as_list(rec.current.claimset.get("claims")) if isinstance(c, dict)}
    for cid, x in rec.novelty().items():
        closest = ", ".join(str(_as_dict(c).get("target")) for c in _as_list(x.get("closest"))) or "none found"
        doubt = _as_list(x.get("comments"))
        state = f"in question ({', '.join(short(k) for k in doubt)})" if doubt else "agrees with the search"
        print(f"  {cid} novelty {labels.get(cid)}: {state}; closest: {closest}")
    for c in comments.values():
        flag = "*" if c.unresolved else " "
        print(f"{flag} {short(c.id)} [{c.comment.get('category')}, {c.comment.get('severity')}] "
              f"{c.state} on {', '.join(c.claims)}: {c.comment.get('comment')}")
        if c.unresolved and c.test:
            print(f"    test: {_test_line(c.test)}")
    if due:
        print("A revision answers every comment marked *: `claimset revision respond`.")
    return 0


def cmd_check_review(args):
    rec = Record(args.record)
    rep = check_review(rec, _load(args.file))
    _print_report(rep, args.json)
    return 1 if rep.errors else 0


def cmd_respond(args):
    rec = Record(args.record)
    response, todo = respond(rec, _load(args.revision), lab=args.lab)
    text = json.dumps(response, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"draft response written to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    if todo:
        print("To complete:", file=sys.stderr)
        for t in todo:
            print(f"  - {t}", file=sys.stderr)
    return 0


def cmd_check(args):
    rec = Record(args.record)
    rep, ops = check_revision(rec, _load(args.revision), _load(args.response), lab=args.lab)
    _print_report(rep, args.json, {"changes": ops})
    if not args.json and not rep.errors:
        print(f"The platform will record {len(ops)} changes from v{rec.current.n} "
              f"(`claimset revision patch` shows them).")
    return 1 if rep.errors else 0


def cmd_patch(args):
    rec = Record(args.record)
    ops = patch(rec.current.claimset, _load(args.revision), _load(args.response) if args.response else {})
    print(json.dumps([op for op, _ in ops], ensure_ascii=False, indent=2))
    return 0


def _run(func):
    def wrapped(args):
        try:
            return func(args)
        except RevisionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    return wrapped


def add_parser(sub):
    p = sub.add_parser("revision", help="reviews, responses and revised versions of a published claimset "
                                        "(revision spec)")
    s = p.add_subparsers(dest="revision_command", metavar="action")
    s.required = True

    a = s.add_parser("status", help="the comments on a claimset and their states; what a revision must answer")
    a.add_argument("record", help="the claimset's record folder (v1/, v2/, … as in the registry)")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=_run(cmd_status))

    a = s.add_parser("check-review", help="check a review or resolution against the record (R1-R7)")
    a.add_argument("file")
    a.add_argument("--record", required=True)
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=_run(cmd_check_review))

    a = s.add_parser("respond", help="draft a response to the unresolved comments, for a revised claimset")
    a.add_argument("record")
    a.add_argument("--revision", required=True, help="the revised claimset (a submission)")
    a.add_argument("--lab", help="the revision's lab.jsonl, with the events appended since the current version")
    a.add_argument("-o", "--output", help="write the draft here (default: standard output)")
    a.set_defaults(func=_run(cmd_respond))

    a = s.add_parser("check", help="check a revision submission: claimset, response and lab record (R8-R17)")
    a.add_argument("record")
    a.add_argument("--revision", required=True)
    a.add_argument("--response", required=True)
    a.add_argument("--lab", help="the revision's lab.jsonl")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=_run(cmd_check))

    a = s.add_parser("patch", help="the JSON Patch the platform records for a revision (§5.3)")
    a.add_argument("record")
    a.add_argument("--revision", required=True)
    a.add_argument("--response")
    a.set_defaults(func=_run(cmd_patch))
