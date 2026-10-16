# Claimset Lab Record — Specification v0.4 (draft)

Status: working draft · Date: 2026-10-16 · Supersedes v0.3 · Companion to the claimset specification (`spec/claimset-v0.11.md`)

## Changes since v0.3

- Exact bodies for `plan`, `approval`, `run`, `result` and `deviation` (§5.7–5.10, §5.12).
- How the tool runs a command, captures its output and reads results (§5.9.1); the results file format (§5.10.1).
- Approvals are made by humans (§5.8).
- Rules L4 and L5 stated precisely; new rule L13 (run outputs) (§6).
- Protecting the log: what is prevented and what is detected at each level of protection (§4.5).
- Git anchoring specified: configuration, what is committed, receipts, automatic anchoring, checking against the remote (§7.1); new rule L14 (§6).
- Agent logs specified precisely: how one is started, what an agent sees and may write, the order and layout of a merge, and when a merge is refused (§3.1, §5.16); rule L10 stated precisely; anchors cover agent logs (§7).

## Changes in v0.3 (since v0.2)

Clarifications needed to implement the reference tool. No change to the meaning of v0.2 records.

- Canonical JSON, hash format and header hash defined exactly (§4.1).
- `by` of the `init` event is `null`; an `actor` event may name its own ID in `by` (self-registration) (§4.2).
- Event numbering within each prefix (§4.2).
- Corrections: what `supersedes` means and how references to a corrected event resolve (§4.3).
- Writers take a lock on the log (§4.4).
- New rules L11 (corrections) and L12 (event IDs). New informative commands `show` and `list` (§10).

## Changes in v0.2 (since v0.1)

- Several agents working at once each write their own log, merged into the main log when they finish (§3.1). Event IDs from agent logs carry the actor prefix (`AC2.RN4`); headers gain `origin`.

## 1. Purpose

A lab record is the running record of a piece of research as it happens: the questions asked, the hypotheses proposed and revised, the plans made, every run executed, every result produced, and every decision taken along the way. Agents (and humans) write it while doing the research. The claimset submitted for publication is distilled from it, and the lab record is published alongside the claimset.

Research is iterative: ideas are formed, tested, revised and abandoned. Conventional papers keep only the surviving story. The lab record keeps everything, so that:

- nothing produced during the research is lost or silently overwritten;
- readers can see how many ideas were tried before the published ones, and which results were planned and which were found by exploring;
- negative results are captured as a by-product of doing the work;
- an agent can resume the work after a context reset or hand it to another agent.

Version 0.1 targets computational research (simulation, analysis of existing data, method development, proofs). Laboratory and qualitative research fit the same model, with humans recording data-collection events; dedicated event types for them are an open question.

## 2. Principles

1. **Append only.** Events are never edited or deleted. A correction is a new event that names the event it supersedes.
2. **Recorded by the tool.** Events are written by the lab tool, which assigns sequence numbers, times and hashes. Agents do not write the log by hand.
3. **Results come from code.** Numbers enter the record only from files written by the code that produced them, never typed by an agent.
4. **Plan before run, or say it was exploratory.** A run is confirmatory only if a plan written earlier covers it; every other run is labelled exploratory. Exploration is legitimate; it is just labelled.
5. **Every outcome is recorded.** Failed runs, crashed runs, null results, refuted and abandoned hypotheses are all events.
6. **Exploration cannot confirm itself.** Evidence that suggested a hypothesis cannot also be the evidence that supports it.
7. **Every change of mind has a reason.** Status changes, revisions and deviations from a plan carry a rationale and point at what prompted them.
8. **Resumable.** The current state of the research can always be rebuilt from the log, and the tool summarises it for whoever picks up the work.

## 3. Layout

A lab lives in a directory, normally inside the project's version-controlled repository:

```
lab/
  lab.jsonl              the main event log, one event per line
  agents/<actor>.jsonl   per-agent logs while agents work concurrently (§3.1)
  anchors.jsonl          timestamp anchors of the log (§7)
  runs/<run id>/         per-run outputs: stdout, stderr, results.json, other files
```

`lab.jsonl` is UTF-8 JSON Lines. Every line is one event.

### 3.1 Several agents

When several agents work on one lab at the same time, each can write its own log instead of the main log, for example in its own working copy of the repository. The agent logs are merged into the main log when the agents finish.

1. **Starting.** Every agent is registered by an `actor` event in the main log. `start` then creates the empty file `lab/agents/<actor id>.jsonl`. From then on, every event written by that actor goes to its agent log, until the log is merged.
2. **Chain.** An agent log has its own `seq` (from 1) and its own hash chain. Its first event's `prev` is the header hash of the latest main-log event when that first event was written. That main-log event is the log's **base**: it records which state of the lab the agent worked from.
3. **IDs.** Event IDs in an agent log are prefixed by the actor ID: `AC2.H1`, `AC2.RN4`, numbered within the log. IDs therefore never collide between agents. Run output directories use the same IDs (`runs/AC2.RN4/`).
4. **What an agent sees.** An agent's events may refer to main-log events up to its base and to earlier events in its own log, and to nothing else: not to later main-log events, and not to events in other agent logs.
5. **What an agent may write.** Only its own actor writes an agent log. `init`, `actor` and `merge` events are written only to the main log. A correction (`supersedes`) in an agent log corrects only an event of the same agent log; other events are corrected in the main log after merging. Human approvals are therefore given in the main log, after the log they approve is merged.
6. **Once.** An agent log is merged once. After the merge, the actor's events go to the main log; for new concurrent work, an agent is registered as a new actor.
7. **Merging.** `merge` appends to the main log a `merge` event (§5.16) followed immediately by the events of the agent logs it names, interleaved in order of `at`, each agent's own order kept, ties broken by actor ID, then `seq`. Each merged event keeps its ID, type, `at`, `by`, `supersedes`, `content_hash` and body, gets a new main-log `seq` and `prev`, and records in `origin` its position and header hash in the agent log: `{log: "agents/AC2.jsonl", seq, header_hash}`. The agent log is kept unchanged, so both chains remain verifiable.
8. **Refusal.** Before writing anything, the tool verifies each agent log on its own and checks every event against the main log as it will be after the events before it. If any check fails (for example, a status change that is no longer valid because the main log has moved on), nothing is written and the problem is reported.

Events written directly to the main log have `origin: null`.

## 4. Event structure

Each event has a **header**, written by the tool, and a **body**, specific to the event type:

```json
{"seq": 17, "id": "RN4", "type": "run", "at": "2026-10-15T14:03:22Z", "by": "AC1",
 "prev": "sha256:…", "content_hash": "sha256:…", "supersedes": null, "origin": null,
 "body": { … }}
```

| Field | Notes |
|---|---|
| `seq` | Integer, starting at 1, increasing by exactly 1 per event |
| `id` | Event ID, prefixed by type (§5), unique in the log |
| `type` | Event type (§5) |
| `at` | UTC time the tool wrote the event (ISO 8601 with seconds) |
| `by` | Actor ID (§5.2) of whoever caused the event |
| `prev` | `sha256` of the previous event's header (all header fields, canonical JSON); for `seq` 1, the hash of the empty string |
| `content_hash` | `sha256` of the body (canonical JSON: sorted keys, no insignificant whitespace) |
| `supersedes` | ID of the event this one corrects, or `null` |
| `origin` | For events merged from an agent log: `{log, seq, header_hash}`; otherwise `null` (§3.1) |
| `body` | Type-specific content |

The headers form a hash chain. Because each header commits to its body only through `content_hash`, a body can be withheld at publication (§8) without breaking the chain: a reader can still verify the order, timing and integrity of every event.

### 4.1 Canonical JSON and hashes

- **Canonical JSON** of a value is its JSON serialisation with object keys sorted, no whitespace outside strings (separators `,` and `:`), non-ASCII characters written as UTF-8 rather than escaped, encoded as UTF-8 bytes. (In Python: `json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.)
- Every hash is written `sha256:` followed by 64 lowercase hexadecimal digits.
- `content_hash` is the SHA-256 of the canonical JSON of `body`.
- The **header hash** of an event is the SHA-256 of the canonical JSON of an object holding all header fields: `seq`, `id`, `type`, `at`, `by`, `prev`, `content_hash`, `supersedes`, `origin`. The `prev` of the next event is this value.
- For `seq` 1, `prev` is the SHA-256 of the empty byte string.

### 4.2 Actors and IDs

- `by` is an actor ID. Two exceptions: the `init` event has `by: null`, and an `actor` event may name its own ID (an actor registering itself).
- An event ID is its type prefix (§5) followed by a positive integer: `H1`, `H2`, `RN17`. Within each prefix, numbers are allocated in order: the next number is one more than the highest used so far in the log. Events from agent logs carry the actor prefix (`AC2.H1`) and are numbered within their agent log, independently of the main log (§3.1).

### 4.3 Corrections

Events are never edited. To correct one, write a new event of the **same type** whose header names the old one in `supersedes`. The new event has its own ID and a complete new body. An event can be superseded at most once, so corrections form a chain.

The first event of a chain is the **root**. Other events keep referring to the root's ID (or any ID in the chain), and every reference resolves to the latest event of the chain. Status changes, checks and evidence that point at a hypothesis therefore still apply after its statement is corrected. A correction is for errors in what was recorded (a typo, a wrong reference); a change of mind about a hypothesis is a `status` event with `to: revised` and a new hypothesis.

### 4.4 Writing

Only the tool writes the log. A writer takes an exclusive lock, the file `lab/.lock` created atomically, appends one complete line and releases the lock. A lock older than 60 seconds is treated as stale. Several agents working at the same time write separate logs (§3.1) rather than contending for one lock.

### 4.5 Protecting the log

An agent that can run commands as the researcher can, in principle, do anything the researcher can do to a file, including rewriting the whole log and recomputing every hash. Protection therefore comes in levels:

| Level | How | Effect |
|---|---|---|
| Tamper-evident | the hash chain (§4.1) and `verify` | edits, deletions and reordering are detected, unless the whole chain is rewritten |
| Guarded | the tool's guard, installed as a hook in the agent's harness, refuses tool calls that would modify the lab directory other than through `claimset lab` | accidental or casual modification is prevented; a determined agent can work around it |
| Append-only file | the operating system's append-only attribute on the log files (on Linux, `chattr +a`, set by an administrator) | the operating system refuses any change except appending, as long as the agent cannot act as an administrator |
| Anchored | the head of the chain is regularly recorded outside the agent's reach (§7) | any rewriting before an anchor is detected, including a full recomputation of the chain |
| Remote append-only | the authoritative copy is held by a service that accepts only appends (the Claimset platform; or a git branch protected against force-pushes and deletion, pushed to with credentials that cannot change the protection) | rewriting is prevented, not just detected |

Locally, the log is tamper-evident. With anchoring, tampering is always detected. With a remote append-only copy, it is prevented. A lab that matters should use at least anchoring.

## 5. Event types

### 5.1 `init` (one per lab, `seq` 1, ID `INIT`)

`{lab_spec_version, working_title, repository, started_at}`. Its `by` is `null`.

### 5.2 `actor` (prefix `AC`)

Registers a person or agent who will write events. Mirrors claimset contributors.

`{kind: agent | human | organisation, name, orcid, models: [{provider, name, version, identifier}], record_source: harness | user_reported | self_reported}`

### 5.3 `question` (prefix `Q`)

`{text}`: a research question or aim.

### 5.4 `hypothesis` (prefix `H`)

`{statement, question, origin, prompted_by}`

- `statement`: follows the claimset statement rules (one assertion, self-contained, scope stated, no rhetoric).
- `question`: the `Q` it addresses.
- `origin`: `a_priori` (proposed before looking at results) or `from_exploration` (suggested by results).
- `prompted_by`: required for `from_exploration`: the result, run or note IDs that suggested it.

A revised hypothesis is a new `H` event; the old one gets a `status` event with `to: revised` and `revised_to` naming the new one.

### 5.5 `status` (prefix `ST`)

`{target, to, rationale, evidence, revised_to}`

- `target`: a hypothesis ID.
- `to`: `testing` · `supported` · `not_supported` · `inconclusive` · `revised` · `abandoned`.
- `rationale`: required, self-contained.
- `evidence`: required for `supported`, `not_supported` and `inconclusive`: run and result IDs.
- `revised_to`: required for `revised`.

`abandoned` means dropped without a conclusive test; `not_supported` means tested and not supported.

### 5.6 `assumption` (prefix `AS`)

`{statement, category, check, check_evidence}`. Categories and check values as in the claimset specification (§3.8).

### 5.7 `plan` (prefix `PL`)

`{kind, hypotheses, design, analysis, criterion, data}`

- `kind`: `confirmatory` or `exploratory`. Required.
- `hypotheses`: hypothesis IDs the plan tests. Required (at least one) for `confirmatory`.
- `design`: what will be run, on what, with which settings. Required.
- `analysis`: what will be computed from the outputs. Required.
- `criterion`: how the outcome will be judged (e.g. "supported if the KL divergence is below 0.01 in every cell"). Required for `confirmatory`.
- `data`: optional list of `{description, hash}` for data that exists when the plan is written.

Plans are immutable. A changed plan is a new plan that supersedes the old one; runs already made under the old plan keep pointing at it.

### 5.8 `approval` (prefix `AP`)

`{target, comment}`: a human approves a plan (or another event) before it is acted on. The `by` of an approval is an actor of kind `human`. Optional; lets a supervisor sign off confirmatory plans.

### 5.9 `run` (prefix `RN`), written by the tool

`{plan, label, command, code_version, dirty, parameters, seeds, data, environment, started_at, ended_at, exit_status, exit_code, outputs}`

- `plan`: plan ID or `null`.
- `label`: `confirmatory` (only if rule L5 holds) or `exploratory`.
- `command`: the command as a list of arguments.
- `code_version`: commit hash of the working directory's repository, or `null` if it is not a git repository; `dirty`: true if it had uncommitted changes outside the lab directory.
- `parameters`: optional object of named settings given to the tool; `seeds`: optional list of random seeds given to the tool.
- `data`: list of `{path, hash}` for input files declared to the tool.
- `environment`: `{software: {python, platform}, hardware: {machine, cpus}}`, as recorded by the tool.
- `started_at`, `ended_at`: UTC times as in `at`.
- `exit_status`: `success` (exit code 0) · `failed` (non-zero exit code) · `crashed` (killed by a signal, or the command could not be started) · `cancelled` (interrupted by the user).
- `exit_code`: the process's exit code, or `null`.
- `outputs`: list of `{path, hash}` for every file in the run's directory after the run, with paths relative to that directory.

#### 5.9.1 How the tool runs a command

1. It checks rule L5 before starting. A run is confirmatory only when it names a confirmatory plan; otherwise, or when asked, it is exploratory.
2. It creates a pending run directory under `runs/` and starts the command with two environment variables: `CLAIMSET_RUN_DIR` (the directory, for any output files) and `CLAIMSET_RESULTS` (the path of `results.json` inside it).
3. It writes the command's standard output and standard error to `stdout.txt` and `stderr.txt` in that directory, and shows them as the command runs.
4. When the command ends, however it ends, it hashes every file in the directory, writes the `run` event, and renames the directory to `runs/<run id>/`.
5. If `results.json` exists, it writes one `result` event per entry (§5.10.1).
6. It exits with the command's exit code.

### 5.10 `result` (prefix `RS`), written by the tool

`{run, quantity, value, unit, interval, conditions, source}`

Same fields as claimset results (`quantity`, `value`, `unit`, `interval`, `conditions`), plus `run` and `source`: the output file the value was read from, whose path is in the run's `outputs`.

#### 5.10.1 Results file

A script reports results by writing a JSON array to the file named by `CLAIMSET_RESULTS`:

```json
[{"quantity": "largest KL divergence per observation, mixture vs exact", "value": 0.00115,
  "conditions": {"distribution": "Gamma", "form": "conventional"}},
 {"quantity": "share of failed fits", "value": 0.18, "unit": "proportion",
  "interval": {"level": 0.95, "lower": 0.15, "upper": 0.21}}]
```

Each entry needs `quantity` (text) and `value` (a number); `unit`, `interval` and `conditions` are optional. Values are written exactly as computed. An entry that is not valid produces no event and is reported by the tool.

### 5.11 `decision` (prefix `DC`)

`{text, prompted_by, affects}`: a choice that changes the direction of the work (e.g. switching metric, dropping a model class, changing the data). This is where the "garden of forking paths" is recorded.

### 5.12 `deviation` (prefix `DV`)

`{plan, run, text, reason}`: a run that departed from its plan, and why. `plan` and `run` are required, `text` says what differed, and `reason` (required) says why.

### 5.13 `check` (prefix `CK`)

`{target, outcome, extent, notes, resolution}`: an independent check of a hypothesis, result or derivation; same semantics as claimset checks (claimset §5). The checking actor must not have written the target.

### 5.14 `artefact` (prefix `AR`)

`{kind, path_or_uri, hash, licence, description}`: registers a file that is not a run output (data set, figure, manuscript, proof file).

### 5.15 `note` (prefix `NT`)

`{text, refers_to}`: an observation that is not yet a hypothesis, decision or result. Notes are kept but not exported as claims.

### 5.16 `merge` (prefix `MG`), written by the tool

`{agents, events_merged}`: records that the agent logs of the listed actors were merged into the main log (§3.1). The merge event is followed immediately by exactly `events_merged` events, each with an `origin` in one of the listed logs.

## 6. Rules

L1. No line of `lab.jsonl` is modified or removed after it is written.
L2. `seq` increases by exactly 1; every `prev` matches the previous header; every `content_hash` matches its body (or its withheld body, §8).
L3. Every executed command started through the tool produces a `run` event, whatever its exit status.
L4. `result` events are written only by the tool. A result names an earlier `run`, and its `source` is a path listed in that run's `outputs`.
L5. A run is labelled `confirmatory` only if it names a `confirmatory` plan, that plan was written earlier (lower `seq`) and, when anchoring is in use, anchored before the run started (§7). Otherwise it is `exploratory`. A run labelled `exploratory` may name any plan or none.
L6. A `status` event moving a hypothesis to `supported` must not list as evidence any event in that hypothesis's `prompted_by`, nor results of runs listed there.
L7. Every `status`, `decision` and `deviation` has a rationale or reason; every `revised` status names `revised_to`.
L8. Every `by` names an `actor` event with a lower `seq`.
L9. A `check` is not made by the actor who wrote its target.
L10. Agent logs follow L1–L9 and L11–L12 on their own, starting from their base, and the restrictions of §3.1 (who writes them, what they may refer to and correct, which event types they hold). A merged event equals the event its `origin` names, whose header hash it records; an agent log is merged once, and nothing is added to it afterwards. Only merged events carry an actor prefix in the main log.
L11. A `supersedes` reference names an earlier event of the same type that has not already been superseded.
L12. Every `id` has the prefix of its `type` and is unique in the log; every reference in a body names an event with a lower `seq`.
L13. The files of a run directory match the hashes in the run's `outputs`. Run directories are never changed after the run.
L14. Every entry in `anchors.jsonl` names a `seq` present in the log, and its `head_hash` equals the header hash of the event at that `seq`; the same holds for each agent log the entry names. A mismatch means the log was rewritten after it was anchored.

The tool enforces L1–L5 and L8–L14 mechanically, and refuses L6 and L7 violations.

## 7. Timestamp anchoring

A timestamp written by the tool proves order, not time. To make "this plan existed before this run" verifiable to others, the tool periodically anchors the head of the chain (the latest `seq` and its header hash) with an external service and records the receipt in `anchors.jsonl`:

`{seq, head_hash, service, receipt, at, agents}`

`agents` (omitted when there are none) records the head of each non-empty agent log in the same way: `{"AC2": {seq, head_hash}}`. An event is covered by an anchor if the anchor's `seq` is at least the event's main-log `seq`, or, for an event written to an agent log, if the anchor's entry for that log has a `seq` at least the event's `seq` there.

Services, in decreasing order of strength:
- `platform`: the Claimset platform records the hash and returns a signed receipt.
- `rfc3161`: a trusted timestamp authority under RFC 3161.
- `git_remote`: a commit containing the log pushed to a remote host (weaker: the host's clock and history are trusted).
- `none`: no anchoring.

The anchor level of a plan becomes `planned_at_anchor` in the exported claimset.

**Automatic anchoring.** When anchoring is configured, the tool anchors after every `plan` event, and before a confirmatory run it makes sure the run's plan is covered by an anchor. If that anchor cannot be made, the run is refused as confirmatory (rule L5); it can still be run as exploratory. The tool can also be configured to anchor after every event.

### 7.1 Git anchoring

With `git_remote` anchoring, the head is recorded by pushing the lab's logs to a dedicated branch of a git remote.

- **Configuration** is kept in `lab/config.json`: `{"anchor": {"service": "git_remote", "remote": "origin", "branch": "claimset-lab", "every": "plan"}}`. `every` is `plan` (default) or `event`.
- **What is committed:** `lab.jsonl`, `anchors.jsonl` and the files in `agents/`, at their paths in the repository. The commit is made on the anchor branch without touching the researcher's working tree or current branch. Run outputs are not committed; they are covered by the hashes in the log.
- **Order:** before committing, the tool checks that each log already on the remote branch (main and agent logs) is a prefix of the local one. If it is not, the local log has been rewritten since the last anchor; the tool refuses to anchor and reports it.
- **Pushing** never forces. A rejected push means the remote branch has changed in a way the tool did not make, and is reported.
- **Receipt:** `{remote, branch, commit}` recorded in the `anchors.jsonl` entry, which itself is committed with the next anchor.
- **Protection of the branch:** to make the remote copy append-only, protect the branch on the host so that force-pushes and deletion are refused, and give the agent credentials that can push but cannot change branch protection. This turns detection into prevention.
- **Checking:** `verify --remote` fetches the branch and checks that the log there is a prefix of the local log and that each anchor's commit holds the log as it was at that anchor.

## 8. Publication and redaction

By default the whole lab record is published with the claimset, as an artefact of kind `lab_record`, together with run outputs.

Individual events can be withheld. A withheld event keeps its header and replaces its body with:

`{"withheld": true, "reason": personal_data | commercial | legal | ethics | security | other, "comment": "…"}`

The header still carries the original `content_hash`, so the chain verifies and the platform's audit agents can check the original if it is provided to them. Run output files can be withheld the same way, keeping their hashes. Withheld events lower the verifiability of the claims that depend on them (claimset §5).

## 9. Export to a claimset

`export` produces a draft claimset submission from the record. The claimset-submit workflow then completes it (principal claims, wording, scope review, guarantor and licence).

| Lab record | Claimset |
|---|---|
| `actor` | contributors and models |
| `hypothesis` with final status `supported`, `not_supported` or `inconclusive` | claim, with `hypothesis: {lab_event, outcome}` |
| `hypothesis` with final status `abandoned` or `revised` | not a claim; counted in `paper.exploration` |
| `run` and its `result`s | evidence object with `results`, `execution` and `lab_runs` |
| `assumption` | assumption claim |
| `check` | claim `checks` |
| `plan` | `planning`, `planned_at`, `planned_at_anchor` on the claims it tested |
| `deviation` | `planning: confirmatory_with_deviations`, and a limitation on the affected claims |
| the whole record | artefact of kind `lab_record`, referenced from `paper.exploration.lab_record` |

**Planning label of an exported claim:**
- `confirmatory`: supported or refuted by a confirmatory run under a plan that named the hypothesis, with no deviation.
- `confirmatory_with_deviations`: as above, but a deviation was recorded.
- `exploratory`: every other case.

**Negative results** are exported as claims in the same claimset: the statement says what was not found, under what scope, and `hypothesis.outcome` is `not_supported`.

## 10. Tool commands (informative)

The reference tool is part of the `claimset` package. Indicative commands:

| Command | Effect |
|---|---|
| `claimset lab init` | creates `lab/`, writes `init` |
| `claimset lab actor` | registers an actor |
| `claimset lab question` / `hypothesis` / `assume` / `note` | writes the corresponding event |
| `claimset lab status H3 supported --evidence RS12 RS13 --why "…"` | status change, with L6 and L7 checked |
| `claimset lab plan` | writes a plan and anchors it |
| `claimset lab approve PL2` | human approval |
| `claimset lab run [--plan PL2] [--exploratory] [--param k=v] [--seed N] [--data FILE] -- <command>` | runs the command, records `run` and `result` events (§5.9.1) |
| `claimset lab decide` / `deviate` / `check` / `artefact` | writes the corresponding event |
| `claimset lab summary` | current state: open questions, hypotheses by status, pending plans, recent decisions; for an actor with an open agent log, what that agent sees |
| `claimset lab show H3` | one event, with corrections resolved |
| `claimset lab list [--type hypothesis] [--last 20]` | events in order, one line each |
| any writing command `--supersedes ID` | writes a correction of `ID` (§4.3) |
| `claimset lab start --by AC2` | starts an agent log for AC2: its events go there until merged (§3.1) |
| `claimset lab merge [AC2 AC3]` | merges agent logs (default: all unmerged ones) into the main log (§3.1) |
| `claimset lab verify` | checks the main and agent chains, the rules and the run files |
| `claimset lab anchor [--git] [--remote origin] [--branch claimset-lab] [--every plan\|event]` | anchors the head of the chain; the first use with `--git` records the configuration (§7.1) |
| `claimset lab verify --remote` | also checks the log against the anchor branch on the remote |
| `claimset lab guard` | the guard for agent harnesses: reads a proposed tool call as JSON on standard input and exits with code 2 if it would modify the lab directory (§4.5); `--print-config` prints the Claude Code hook configuration |
| `claimset lab protect` | sets the operating system's append-only attribute on the log files, or explains how an administrator can (§4.5) |
| `claimset lab publish --withhold RN7 --reason personal_data --comment "…"` | prepares the publishable record |
| `claimset lab export` | drafts the claimset submission |

## 11. Open questions

1. Anchoring service: platform receipts, RFC 3161 providers, or both.
2. Storage of large run outputs (content-addressed storage, external archives).
3. Event types for laboratory and qualitative research (data-collection sessions, instruments, coding rounds).
4. Very long explorations (thousands of runs): summarised export without losing the counts.
5. Whether abandoned hypotheses with substantial evidence should be exportable as claims.
