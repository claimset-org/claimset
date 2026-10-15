# Claimset Lab Record — Specification v0.2 (draft)

Status: working draft · Date: 2026-10-15 · Supersedes v0.1 · Companion to the claimset specification (`spec/claimset-v0.11.md`)

## Changes since v0.1

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

When several agents work on one lab at the same time, each writes its own log instead of the main log:

1. Every agent is registered by an `actor` event in the main log before it starts.
2. The agent writes `lab/agents/<actor id>.jsonl`, with its own `seq` (from 1) and its own hash chain. Its first event's `prev` is the header hash of the latest main-log event when the agent started, so the agent log records which state of the lab it began from.
3. Event IDs in an agent log are prefixed by the actor ID: `AC2.H1`, `AC2.RN4`. IDs therefore never collide between agents. Run output directories use the same IDs (`runs/AC2.RN4/`).
4. An agent's events may refer to main-log events that existed when it started and to earlier events in its own log, but not to events in other agents' logs that have not been merged.
5. When the agent finishes, `merge` appends its events to the main log in order of `at` (ties broken by actor ID, then `seq`). Each merged event gets a new main-log `seq` and `prev` and keeps its ID; its header records `origin: {log, seq, header_hash}`, the event's position and header hash in the agent log. The agent log is kept unchanged, so both chains remain verifiable.
6. Merging several agents at once interleaves their events by `at`. A merge is recorded by a `merge` event in the main log: `{agents, events_merged}`.

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

## 5. Event types

### 5.1 `init` (one per lab, `seq` 1)

`{lab_spec_version, working_title, repository, started_at}`

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

- `kind`: `confirmatory` or `exploratory`.
- `hypotheses`: hypothesis IDs the plan tests (required for `confirmatory`).
- `design`: what will be run, on what, with which settings.
- `analysis`: what will be computed from the outputs.
- `criterion`: for `confirmatory`, how the outcome will be judged (e.g. "supported if the KL divergence is below 0.01 in every cell").
- `data`: description and content hashes of the data, if it exists.

Plans are immutable. A changed plan is a new plan that supersedes the old one; runs already made under the old plan keep pointing at it.

### 5.8 `approval` (prefix `AP`)

`{target, comment}`: a human approves a plan (or another event) before it is acted on. Optional; lets a supervisor sign off confirmatory plans.

### 5.9 `run` (prefix `RN`), written by the tool

`{plan, label, command, code_version, dirty, parameters, seeds, data_hashes, environment, started_at, ended_at, exit_status, outputs}`

- `plan`: plan ID or `null`.
- `label`: `confirmatory` (only if rule L5 holds) or `exploratory`.
- `code_version`: commit hash; `dirty`: true if the working tree had uncommitted changes.
- `environment`: `{software, hardware}`.
- `exit_status`: `success` · `failed` · `crashed` · `cancelled`.
- `outputs`: list of `{path, hash}` for every file the run wrote under `runs/<run id>/`.

### 5.10 `result` (prefix `RS`), written by the tool

`{run, quantity, value, unit, interval, conditions, source}`

Same fields as claimset results (`quantity`, `value`, `unit`, `interval`, `conditions`), plus `run` and `source` (the output file the value was read from, whose hash is in the run's `outputs`). Scripts report results by writing them to the file named in the environment variable `CLAIMSET_RESULTS`; the tool reads that file after the run and writes one result event per entry.

### 5.11 `decision` (prefix `DC`)

`{text, prompted_by, affects}`: a choice that changes the direction of the work (e.g. switching metric, dropping a model class, changing the data). This is where the "garden of forking paths" is recorded.

### 5.12 `deviation` (prefix `DV`)

`{plan, run, text, reason}`: a run that departed from its plan, and why.

### 5.13 `check` (prefix `CK`)

`{target, outcome, extent, notes, resolution}`: an independent check of a hypothesis, result or derivation; same semantics as claimset checks (claimset §5). The checking actor must not have written the target.

### 5.14 `artefact` (prefix `AR`)

`{kind, path_or_uri, hash, licence, description}`: registers a file that is not a run output (data set, figure, manuscript, proof file).

### 5.15 `note` (prefix `NT`)

`{text, refers_to}`: an observation that is not yet a hypothesis, decision or result. Notes are kept but not exported as claims.

### 5.16 `merge` (prefix `MG`), written by the tool

`{agents, events_merged}`: records that the listed agent logs were merged into the main log (§3.1).

## 6. Rules

L1. No line of `lab.jsonl` is modified or removed after it is written.
L2. `seq` increases by exactly 1; every `prev` matches the previous header; every `content_hash` matches its body (or its withheld body, §8).
L3. Every executed command started through the tool produces a `run` event, whatever its exit status.
L4. `result` events are written only by the tool, from a file listed in the run's `outputs`.
L5. A run is labelled `confirmatory` only if it names a `confirmatory` plan, that plan was written earlier (lower `seq`) and, when anchoring is in use, anchored before the run started (§7). Otherwise it is `exploratory`.
L6. A `status` event moving a hypothesis to `supported` must not list as evidence any event in that hypothesis's `prompted_by`, nor results of runs listed there.
L7. Every `status`, `decision` and `deviation` has a rationale or reason; every `revised` status names `revised_to`.
L8. Every `by` names an `actor` event with a lower `seq`.
L9. A `check` is not made by the actor who wrote its target.
L10. Agent logs follow L1–L9 on their own; merged events keep their IDs and record their `origin`; an agent log is not merged twice.

The tool enforces L1–L5 and L8–L10 mechanically, and refuses L6 and L7 violations.

## 7. Timestamp anchoring

A timestamp written by the tool proves order, not time. To make "this plan existed before this run" verifiable to others, the tool periodically anchors the head of the chain (the latest `seq` and its header hash) with an external service and records the receipt in `anchors.jsonl`:

`{seq, head_hash, service, receipt, at}`

Services, in decreasing order of strength:
- `platform`: the Claimset platform records the hash and returns a signed receipt.
- `rfc3161`: a trusted timestamp authority under RFC 3161.
- `git_remote`: a commit containing the log pushed to a remote host (weaker: the host's clock and history are trusted).
- `none`: no anchoring.

The tool anchors automatically after every `plan`, before the first run that uses it. The anchor level of a plan becomes `planned_at_anchor` in the exported claimset.

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
| `claimset lab run [--plan PL2] -- <command>` | runs the command, records `run` and `result` events |
| `claimset lab decide` / `deviate` / `check` / `artefact` | writes the corresponding event |
| `claimset lab summary` | current state: open questions, hypotheses by status, pending plans, recent decisions |
| `claimset lab merge [AC2 AC3]` | merges finished agent logs into the main log (§3.1) |
| `claimset lab verify` | checks the main and agent chains and rules L1–L10 |
| `claimset lab anchor` | anchors the head of the chain |
| `claimset lab publish --withhold RN7 --reason personal_data --comment "…"` | prepares the publishable record |
| `claimset lab export` | drafts the claimset submission |

## 11. Open questions

1. Anchoring service: platform receipts, RFC 3161 providers, or both.
2. Storage of large run outputs (content-addressed storage, external archives).
3. Event types for laboratory and qualitative research (data-collection sessions, instruments, coding rounds).
4. Very long explorations (thousands of runs): summarised export without losing the counts.
5. Whether abandoned hypotheses with substantial evidence should be exportable as claims.
