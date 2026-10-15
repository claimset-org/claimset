"""Running a command under the lab record (lab record spec §5.9.1)."""

import json
import os
import platform
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from .anchor import anchoring, ensure_covered
from .core import LabError, file_hash, is_withheld

RESULTS_FILE = "results.json"


def _git(args, cwd):
    try:
        out = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def code_version(cwd, lab_root):
    """(commit, dirty) of the repository containing cwd; (None, None) outside a repository."""
    commit = _git(["rev-parse", "HEAD"], cwd)
    if commit is None:
        return None, None
    top = _git(["rev-parse", "--show-toplevel"], cwd)
    args = ["status", "--porcelain", "--", "."]
    try:
        rel = Path(lab_root).resolve().relative_to(Path(top).resolve())
        args.append(f":(exclude){rel.as_posix()}")
    except (ValueError, TypeError):
        pass
    status = _git(args, top or cwd)
    return commit, bool(status)


def environment():
    return {"software": {"python": platform.python_version(), "platform": platform.platform()},
            "hardware": {"machine": platform.machine(), "cpus": os.cpu_count()}}


def _tee(src, dest_file, echo_stream):
    """Copy a process stream to a file and, if given, to the terminal as it arrives."""
    raw = getattr(echo_stream, "buffer", None)
    with open(dest_file, "wb") as fh:
        while True:
            chunk = src.read1(65536)
            if not chunk:
                break
            fh.write(chunk)
            if echo_stream is None:
                continue
            if raw is not None:
                raw.write(chunk)
                raw.flush()
            else:
                echo_stream.write(chunk.decode("utf-8", "replace"))
                echo_stream.flush()


def _outputs(rdir):
    files = sorted(p for p in rdir.rglob("*") if p.is_file())
    return [{"path": str(p.relative_to(rdir)).replace(os.sep, "/"), "hash": file_hash(p)} for p in files]


def _parse_results(path):
    """Valid result entries and a list of problems with the rest."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"{RESULTS_FILE} could not be read: {exc}"]
    if not isinstance(data, list):
        return [], [f"{RESULTS_FILE} must hold a JSON array of results"]
    good, problems = [], []
    for i, r in enumerate(data):
        v = r.get("value") if isinstance(r, dict) else None
        if not isinstance(r, dict) or not r.get("quantity") or not isinstance(v, (int, float)) or isinstance(v, bool):
            problems.append(f"entry {i}: needs 'quantity' (text) and 'value' (a number)")
            continue
        good.append({k: r[k] for k in ("quantity", "value", "unit", "interval", "conditions") if r.get(k) is not None})
    return good, problems


def run_command(lab, by, argv, plan=None, exploratory=False, parameters=None, seeds=None, data=None,
                echo=True, cwd=None):
    """Run argv under the lab record.

    Returns {"run": event, "results": [events], "exit_code": int|None, "problems": [str]}.
    Raises LabError before starting if the run would break a rule.
    """
    if not argv:
        raise LabError("schema", "run: give the command after --")
    cwd = cwd or os.getcwd()
    st = lab.state()
    label = "exploratory"
    if plan is not None:
        p = st.get(plan)
        if p is None or p["type"] != "plan":
            raise LabError("L12", f"run: '{plan}' is not a plan")
        latest = st.latest(plan)
        if not exploratory and not is_withheld(latest) and latest["body"].get("kind") == "confirmatory":
            label = "confirmatory"
    if label == "confirmatory" and anchoring(lab):
        try:
            ensure_covered(lab, st.latest(plan)["seq"])
        except LabError as exc:
            raise LabError("L5", f"run: plan {plan} could not be anchored before the run ({exc.message}); "
                                 f"fix anchoring or run with --exploratory") from None
    data_entries = []
    for d in data or []:
        if not Path(d).is_file():
            raise LabError("schema", f"run: data file {d} does not exist")
        data_entries.append({"path": d, "hash": file_hash(d)})

    probe = {"plan": plan, "label": label, "command": list(argv), "exit_status": "success",
             "outputs": [], "data": data_entries}
    st.check("run", st.next_id("run"), by, probe, None)

    runs_dir = lab.root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    rdir = runs_dir / f".pending-{uuid.uuid4().hex}"
    rdir.mkdir()
    env = dict(os.environ, CLAIMSET_RUN_DIR=str(rdir.resolve()),
               CLAIMSET_RESULTS=str((rdir / RESULTS_FILE).resolve()))

    commit, dirty = code_version(cwd, lab.root)
    started = lab.clock()
    exit_code, status = None, "crashed"
    try:
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        (rdir / "stdout.txt").write_bytes(b"")
        (rdir / "stderr.txt").write_text(f"claimset: could not start the command: {exc}\n", encoding="utf-8")
        proc = None
    if proc is not None:
        threads = [threading.Thread(target=_tee, args=(proc.stdout, rdir / "stdout.txt", sys.stdout if echo else None)),
                   threading.Thread(target=_tee, args=(proc.stderr, rdir / "stderr.txt", sys.stderr if echo else None))]
        for t in threads:
            t.start()
        try:
            exit_code = proc.wait()
            status = "success" if exit_code == 0 else ("crashed" if exit_code < 0 else "failed")
        except KeyboardInterrupt:
            proc.terminate()
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = proc.wait()
            status = "cancelled"
        for t in threads:
            t.join()
        proc.stdout.close()
        proc.stderr.close()
    ended = lab.clock()

    body = {"plan": plan, "label": label, "command": list(argv), "code_version": commit, "dirty": dirty,
            "parameters": parameters or None, "seeds": seeds or None, "data": data_entries or None,
            "environment": environment(), "started_at": started, "ended_at": ended,
            "exit_status": status, "exit_code": exit_code, "outputs": _outputs(rdir)}
    body = {k: v for k, v in body.items() if v is not None}
    try:
        run_event = lab.append("run", body, by=by)
    except LabError:
        raise LabError("L3", f"the command ran but its run event could not be written; outputs kept in {rdir}")
    final = runs_dir / run_event["id"]
    if final.exists():
        raise LabError("L13", f"{final} already exists; the outputs of {run_event['id']} are kept in {rdir}")
    rdir.rename(final)

    results, problems = [], []
    if (final / RESULTS_FILE).is_file():
        entries, problems = _parse_results(final / RESULTS_FILE)
        for r in entries:
            r = dict(r, run=run_event["id"], source=RESULTS_FILE)
            results.append(lab.append("result", r, by=by))
    return {"run": run_event, "results": results, "exit_code": exit_code, "problems": problems}
