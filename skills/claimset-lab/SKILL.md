---
name: claimset-lab
description: Keep a Claimset lab record (an append-only, hash-chained log of questions, hypotheses, plans, runs, results and decisions) while doing computational research, so that planned tests can be told apart from exploration and every reported number traces back to the code that produced it. Use when starting or continuing research such as experiments, simulations, benchmarks or data analyses; when a project has a lab/ directory with lab.jsonl; or when asked to keep a lab record, research log or pre-registration.
---

# Keeping a lab record

A lab record is the running log of a piece of research, kept while it happens. It makes three things checkable later:
- which tests were **planned before** their results were seen, and which results came from exploration;
- that every number comes from a recorded run of code, not from memory or retyping;
- what was tried and failed, so a positive result can be weighed against the number of attempts.

At the end the record is exported to a claimset (a paper for LLM readers) with the `claimset-submit` skill. The specification is `references/lab-record-v0.4.md`; read §5 (event types) and §6 (rules) when an event or a refusal is unclear.

**The tool.** Use `claimset` if it is installed (`claimset --help` works). Otherwise use the copy bundled with this skill, which needs only Python 3.9+: `python3 <this skill>/scripts/claimset`. Below, `claimset` stands for whichever you use. Every writing command prints the new event's ID; add `--json` to see the whole event. The lab lives in `./lab` unless `--lab DIR` or `$CLAIMSET_LAB` says otherwise.

## Rules you never break

1. **Write to `lab/` only through `claimset lab`.** Never edit, delete, move, truncate or overwrite anything in `lab/`, including `runs/`, by any means: editor tools, shell redirection, scripts, git. Fix mistakes with a correction (`--supersedes`). If a guard hook blocks a command, do not look for another way; use the `claimset lab` command it names.
2. **Write only as yourself.** Pass your own actor ID with `--by` on every writing command. Never use a human's ID. Approvals (`claimset lab approve`) are made by humans only: ask the human to run the command; never run it for them.
3. **Numbers come from code.** Every result enters the record through `claimset lab run`, from the results file your script writes. A rationale or note may quote a number from a result it cites, but never introduce a measured value that no recorded run produced. If a run fails or gives an unwelcome result, it stays recorded; rerun with a reason, never instead of recording.
4. **Plan before you test.** A test counts as confirmatory only if its plan was recorded before the run. Write the plan, then run. Never write a plan after seeing a result and present it as prior.
5. **A result that suggested a hypothesis cannot confirm it.** Record such a hypothesis with `--from-exploration --prompted-by <the run or result>`, then test it on a new run that the plan defines (new data, a held-out split, new seeds). The tool refuses the shortcut (rule L6).
6. **Record what did not work.** A refuted hypothesis gets `not_supported`, a dropped one `abandoned`, both with a reason. Negative results are published like positive ones.
7. **Keep secrets and personal data out of event text.** Refer to data files by path; the tool records their hashes.

## Starting

1. **Check for an existing record.** If `lab/lab.jsonl` exists, this is a continuation: go to *Resuming*.
2. **Create the record:** `claimset lab init --title "Working title" --repository https://…` (the repository URI is used when the record is published).
3. **Register the actors.** Ask the human for their name (and ORCID iD, if they have one). Register them first, then yourself:
   ```
   claimset lab actor --kind human --name "A. Researcher" --orcid 0000-0002-1825-0097      # -> AC1
   claimset lab actor --kind agent --name "Research agent" \
       --model "Anthropic|Claude Opus|5.5|claude-opus-5-5" --record-source self_reported --by AC1   # -> AC2
   ```
   Take provider, model name, version and identifier from your system prompt or environment, and never guess them. `--record-source harness` only if the values come from the system that runs you; otherwise `self_reported`.
4. **Use `--by AC2` (your ID) on every writing command.** Setting `CLAIMSET_ACTOR` also works, but environment variables often do not persist between separate shell calls.
5. **Protection.** Offer the human these steps; together they make the record tamper-proof rather than only tamper-evident (spec §4.5):
   - `claimset lab anchor --git` pushes the record to a `claimset-lab` branch after every plan, so plans are timestamped outside your reach. It needs a git remote. Afterwards, a confirmatory run is refused if its plan cannot be pushed.
   - The guard hook stops tool calls that would modify `lab/`: add the output of `claimset lab guard --print-config` to `.claude/settings.json`. Because it restricts you, let the human decide whether to install it.
   - `sudo claimset lab protect` makes the log files append-only at the operating-system level (Linux). It needs the human, because it needs an administrator.
   - On the git host, protect the `claimset-lab` branch against force-pushes and deletion.

## The research loop

Record events as the work happens, not afterwards from memory.

| When | Command |
|---|---|
| You frame a research question | `claimset lab question "Does method M beat seasonal naive on retail sales?" --by AC2` |
| You state a hypothesis before testing it | `claimset lab hypothesis "M reduces MASE against seasonal naive on monthly series." --question Q1 --by AC2` |
| A result suggests a new hypothesis | `claimset lab hypothesis "…" --question Q1 --from-exploration --prompted-by RN4 --by AC2` |
| You rely on an assumption | `claimset lab assume "Forecast errors are independent across series." --category statistical --check unchecked --by AC2` |
| Before a test that should count as confirmatory | `claimset lab plan --kind confirmatory --hypotheses H1 --design "…" --analysis "…" --criterion "…" --data data/sales.csv --by AC2` |
| The human approves a plan (they run it) | `claimset lab approve PL1 --by AC1` |
| You run any code that produces results | `claimset lab run --plan PL1 --seed 1 --param horizon=12 --data data/sales.csv --by AC2 -- python3 experiment.py` |
| A run departs from its plan | `claimset lab deviate --plan PL1 --run RN3 --text "Used 900 of 1,200 series." --reason "Data gap after 2019." --by AC2` |
| Evidence decides a hypothesis | `claimset lab status H1 supported --why "Criterion of PL1 met: reduction 0.08 > 0." --evidence RS2 --by AC2` |
| You make a choice that shapes the work | `claimset lab decide "Dropped weekly series: too few observations per series." --affects H2 --by AC2` |
| You observe something worth keeping | `claimset lab note "Errors grow sharply after horizon 6." --refers-to RN3 --by AC2` |
| You produce or use a file others need | `claimset lab artefact results/table1.csv --kind table --description "…" --licence CC-BY-4.0 --by AC2` |
| Someone else checks a result | `claimset lab check RN3 --outcome confirmed --extent full --notes "…" --by AC5` (never the author of the target) |

**Hypotheses.** A hypothesis is *a priori* only if no result you have seen informed it. If any result did, record it `--from-exploration` with the runs or results that prompted it; when unsure, choose `--from-exploration`. Write it as a testable statement with its conditions ("For local-level series of 120 observations, …").

**Plans.** Write the design (what runs, on what data, with which settings), the analysis (what is computed from the outputs) and the criterion (how the outcome will be judged, before seeing it) concretely enough that someone else could tell whether a run followed them. A confirmatory plan names the hypotheses it tests. Exploratory plans are optional but useful. Approvals are optional too: when the human is available, ask them to approve confirmatory plans; when not, proceed. The record shows which plans were approved.

**Runs.** `claimset lab run` records the command, code commit (and whether the working tree had uncommitted changes), environment, seeds, parameters, input-file hashes, exit status, stdout, stderr and every output file. Commit your code before confirmatory runs, so the recorded commit is the code that ran; put generated files (`__pycache__/`, outputs written outside the run directory) in `.gitignore`, because any uncommitted or untracked file outside `lab/` marks the run as dirty. Keep `lab/` in the repository with the code and commit it with your work. Without `--plan`, or with `--exploratory`, a run is exploratory; it is confirmatory only under a confirmatory plan. Failed runs are recorded too; that is intended. Checking that a script starts at all, on toy input, may happen outside the tool; any run whose output you look at to form a hypothesis, choose a setting or decide anything goes through `claimset lab run`.

Options that take IDs take several at once (`--hypotheses H1 H2`, `--evidence RS4 RS5`, `--refers-to`, `--prompted-by`, `--affects`). `--seed N`, `--param name=value` (the value is read as JSON when it parses, e.g. `--param grid=[0.1,0.5]`) and `--data FILE` can be repeated.

**Results.** Your script writes its numbers as a JSON array to the file named by `$CLAIMSET_RESULTS`, and any other outputs into `$CLAIMSET_RUN_DIR`. Each entry: `quantity` (what was measured, self-contained), `value` (a number), and optionally `unit`, `interval` `{level, lower, upper}` and `conditions` (the settings that define this result):

```python
import json, os

results = [{"quantity": "mean relative MASE reduction, method M vs seasonal naive",
            "value": 0.081, "interval": {"level": 0.95, "lower": 0.052, "upper": 0.11},
            "conditions": {"frequency": "monthly", "horizon": "1-12"}}]
path = os.environ.get("CLAIMSET_RESULTS")
if path:  # also runs outside the lab
    with open(path, "w") as f:
        json.dump(results, f)
```

In R: `jsonlite::write_json(results, Sys.getenv("CLAIMSET_RESULTS"), auto_unbox = TRUE, digits = NA)`, with `results` a list of lists. Each entry becomes a result event (`RS…`) that statuses and claims can cite. The tool reports entries it could not read.

**Statuses.** `testing` when work on a hypothesis starts; `supported`, `not_supported` or `inconclusive` with `--evidence` (the result or run IDs) and `--why`; `revised` with `--revised-to` the new hypothesis; `abandoned` with `--why`. Every status needs a rationale.

## Example: from exploration to a confirmatory test

```
claimset lab run --by AC2 -- python3 explore.py                 # RN1, exploratory; RS1 shows an effect at long horizons
claimset lab hypothesis "M helps most at horizons 7-12." --question Q1 --from-exploration --prompted-by RN1 --by AC2   # H2
claimset lab plan --kind confirmatory --hypotheses H2 --design "Held-out 2020-2024 series, horizons 7-12" \
    --analysis "Mean relative MASE reduction" --criterion "Reduction > 0 with 95% interval above 0" --by AC2           # PL1
claimset lab run --plan PL1 --by AC2 -- python3 confirm.py --holdout   # RN2, confirmatory; RS2
claimset lab status H2 supported --why "PL1 criterion met on held-out data." --evidence RS2 --by AC2
```

Citing RS1 as evidence for H2 would be refused: RS1 suggested H2.

## Corrections

Every writing command takes `--supersedes ID`: it writes a new event of the same type that replaces ID, which stays in the log. Correct a wrong hypothesis wording, a plan written with a typo, or a status given with the wrong evidence this way. An event can be corrected once; to correct it again, correct the correction. A plan corrected after a run does not change what that run was tested against.

## Several agents at once

If you orchestrate other agents that work concurrently:
1. Register each one as an actor in the main log (`claimset lab actor --kind agent … --by AC2`), then run `claimset lab start --by AC3`. From then on, AC3's events go to `lab/agents/AC3.jsonl`, with IDs like `AC3.H1`.
2. Tell each agent its actor ID and to follow this skill. It sees the main log as it was when it started, plus its own events, and can refer only to those.
3. When the agents finish, run `claimset lab merge --by AC2`. The merge is refused as a whole if an event no longer fits the main log; the tool says which.
4. An agent log is used once. For new concurrent work, register a new actor.

## Resuming after a break or a context reset

The record is your memory. Before doing anything else:

```
claimset lab summary --by AC2          # questions, hypotheses and their status, plans, runs, recent decisions
claimset lab list --last 20            # the latest events
claimset lab show H3                   # one event, with its corrections
```

Find your actor ID in the summary's actor list. If you are a new agent taking over, register yourself as a new actor rather than reusing another agent's ID.

## When the tool refuses

The message names the rule. The usual cases:

| Rule | Meaning | What to do |
|---|---|---|
| L5 | Confirmatory run without a confirmatory plan, or the plan could not be anchored | Write the plan first, or run with `--exploratory` |
| L6 | The evidence suggested the hypothesis | Test it on a new run under a plan |
| L7 | Missing rationale or reason | Add `--why` / `--reason` |
| L8 | Unknown actor | Register it, or fix `--by` |
| L9 | You are checking your own event | Ask another agent or the human |
| L10 | Agent-log restriction | Write that event to the main log after merging |
| L11 | Correction of a different type, or of an event already corrected | Correct the latest version, with the same type |
| L12 | Reference to an event that does not exist (yet), or that your agent log cannot see | Check the ID with `claimset lab list` |

Never work around a refusal by editing files or by writing as another actor.

## Finishing

1. Give every hypothesis a final status (`supported`, `not_supported`, `inconclusive`, `revised` or `abandoned`). Export refuses while any is open.
2. Merge any agent logs, then run `claimset lab verify` (and `claimset lab verify --remote` if anchoring is set up). Fix nothing by hand. If verification fails, tell the human exactly what it reports.
3. `claimset lab export -o claimset.json` writes the draft claimset and prints the completion list. Continue with the `claimset-submit` skill. The draft takes claim statements from the hypotheses, so the list asks you to reword negative results to say plainly what was not found; do that in the claimset, not in the record.
4. Tell the human if anything in the record should not be published (personal data, commercial or legal reasons); the record supports withholding individual events with a stated reason (spec §8).
