# Claimset

Claimset is an open format for research papers written by AI agents for AI readers, and the tools that go with it.

A claimset is a JSON file of atomic, self-contained **claims**, each with its scope, assumptions, limitations, kind and strength of support, and links to the evidence, results and artefacts behind it. Agents write the papers; a human supervisor and guarantor answer for them. Humans read them by asking an LLM.

Research is iterative, so the format has a companion: the **lab record**, an append-only, hash-chained log of questions, hypotheses, plans, runs, results and decisions kept while the research happens. The claimset is distilled from it, negative results included.

> Status: early draft. The format and tools are changing quickly; expect breaking changes before 1.0.

## What is in this repository

| Path | Contents |
|---|---|
| `spec/` | The claimset specification and the lab record specification (normative) |
| `src/claimset/` | The `claimset` Python package and command-line tool |
| `skills/` | Instructions for AI agents (Claude skills), bundling everything they need |
| `examples/` | Valid example claimsets |
| `tests/` | Test suite |
| `tools/` | Maintenance scripts |

## Install

```
pip install git+https://github.com/claimset-org/claimset
```

The package has no dependencies beyond the Python standard library (Python 3.9 or later). It carries the specifications and the agent skills of its version:

```
claimset spec                  # the claimset specification this version validates against
claimset spec --lab            # the lab record specification
claimset skill install         # install or update the skills in .claude/skills (--dir for another place)
claimset skill check           # are the installed skills this version's?
```

Skills copied into a project do not follow later versions of the package; `claimset skill check` finds an out-of-date copy and `claimset skill install` replaces it.

## Use

Validate a claimset before submission:

```
claimset validate claimset.json
claimset validate claimset.json --json        # machine-readable report
claimset validate claimset.json --published   # check a published file
claimset render claimset.json -o claimset.txt  # the reading view: plain text for reading models
```

The report lists errors (the file is rejected), warnings (accepted, recorded) and the verifiability the platform will compute for each claim.

Keep a lab record while doing research:

```
claimset lab init --title "Working title"
claimset lab actor --kind agent --name "Research agent" --model "Anthropic|Claude Opus|5.5"
export CLAIMSET_ACTOR=AC1
claimset lab question "Does X improve Y?"
claimset lab hypothesis "X improves Y on monthly series." --question Q1
claimset lab plan --kind confirmatory --hypotheses H1 --design "…" --analysis "…" \
    --framework frequentist --parameter alpha=0.05 --parameter test="paired t-test" --parameter sides=greater \
    --parameter correction=none --parameter sample="400 series, fixed" \
    --quantity p="p-value" --quantity d="mean difference" --supported "p < alpha and d > 0" --not-supported "…"
claimset lab run --plan PL1 --seed 7 -- python3 experiment.py
claimset lab verdict PL1     # the verdict the plan's rule gives on the run's results
claimset lab status H1 supported --plan PL1 --why "Rule of PL1 met." --evidence RS1 RS2
claimset lab summary     # current state, for whoever picks up the work
claimset lab verify      # hash chain, rules and run files
```

A confirmatory plan fixes everything that decides the outcome before any run: the test, significance level, sidedness, correction for multiple tests, sample size and stopping rule, the results the decision is made on, and the rules for *supported* and *not supported* (anything else is *inconclusive*). The tool computes the verdict from those rules and refuses a status that disagrees or a second attempt at a decided test, and every planned test that ran is exported as a claim, whatever its result. Exploration stays light: record the runs you learn from; nothing else is enforced.

`run` records the command, code version, environment and every output file, and turns the numbers the script writes to `$CLAIMSET_RESULTS` into result events. The log is append-only and hash-chained; the tool refuses writes that break the rules (e.g. confirming a hypothesis with the result that suggested it).

When the research is done, draft the claimset from the record:

```
claimset lab export -o claimset.json
```

The draft carries everything the record states: a claim for every tested hypothesis (negative results included) with its outcome and whether the test was planned, evidence with the runs and results behind it, contributors, models and the exploration counts. Export also prints a completion list of what only people or the writing agent can decide: licence, guarantor, principal claims, wording and scope. The `claimset-submit` skill works through it.

Several agents working at once each keep their own log, merged when they finish:

```
claimset lab start --by AC2     # AC2's events now go to lab/agents/AC2.jsonl, with IDs like AC2.H1
claimset lab start --by AC3
claimset lab merge              # appends both logs to the main log, interleaved by time
```

An agent sees the main log as it was when it started, plus its own events. The merge is refused as a whole if any event no longer fits the main log, and `verify` checks that every merged event matches its agent log.

### Protecting the lab record

The hash chain makes any edit to the log detectable. Three optional layers stop edits from happening or make them impossible to hide (spec §4.5):

```
claimset lab anchor --git     # push the log to a `claimset-lab` branch after every plan
claimset lab verify --remote  # check the local log against the anchored copy
claimset lab protect          # append-only attribute on the log files (Linux, needs sudo)
```

- **Anchoring** commits the log to its own branch without touching your working tree and never force-pushes. A confirmatory run is refused until its plan has been anchored, so the plan provably existed before the results. Protect the branch on your git host against force-pushes and deletion to make the remote copy append-only.
- **The guard** is a hook for agent harnesses that refuses tool calls that would edit or delete files in the lab directory other than through `claimset lab`. For Claude Code, add the output of `claimset lab guard --print-config` to `.claude/settings.json` (see [hooks](https://docs.claude.com/en/docs/claude-code/hooks)).
- **The append-only attribute** makes the kernel refuse anything but appending, as long as the agent cannot use `sudo`.

## For AI agents

`skills/claimset-submit/` teaches an agent to turn finished research, or a lab record, into a valid claimset submission. It is self-contained: copy the folder into your agent's skills directory (for Claude Code, `.claude/skills/` in a project or `~/.claude/skills/`). Agents without skills support can be given its `SKILL.md` as instructions.

`skills/claimset-lab/` teaches an agent to keep a lab record while it does research: registering itself honestly, planning before testing, running code through the tool, recording failures and negative results, working alongside other agents, and handing over to `claimset-submit` at the end. It bundles a copy of the `claimset` package, so it works without installing anything (`python3 scripts/claimset lab ...`).

## Contributing

- The specifications in `spec/` are the source of truth; the validator implements them and the tests check both.
- After changing the validator, a spec or an example, run `python3 tools/sync_skills.py` to update the copies bundled in the skills.
- Run the tests with `python3 -m unittest discover -s tests`.

## Licence

Code is licensed under the Apache License 2.0 (`LICENSE`). The specifications in `spec/` are licensed under Creative Commons Attribution 4.0 International (CC BY 4.0).
