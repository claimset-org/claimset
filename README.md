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

The package has no dependencies beyond the Python standard library (Python 3.9 or later).

## Use

Validate a claimset before submission:

```
claimset validate claimset.json
claimset validate claimset.json --json        # machine-readable report
claimset validate claimset.json --published   # check a published file
```

The report lists errors (the file is rejected), warnings (accepted, recorded) and the verifiability the platform will compute for each claim.

Keep a lab record while doing research:

```
claimset lab init --title "Working title"
claimset lab actor --kind agent --name "Research agent" --model "Anthropic|Claude Opus|5.5"
export CLAIMSET_ACTOR=AC1
claimset lab question "Does X improve Y?"
claimset lab hypothesis "X improves Y on monthly series." --question Q1
claimset lab status H1 testing --why "Plan being written."
claimset lab summary     # current state, for whoever picks up the work
claimset lab verify      # hash chain and rules
```

The log is append-only and hash-chained; the tool refuses writes that break the rules (e.g. confirming a hypothesis with the result that suggested it). Plans, runs, results, per-agent logs and export to a claimset are being added; see `spec/lab-record-v0.3.md` §10.

## For AI agents

`skills/claimset-submit/` teaches an agent to turn finished research, or a lab record, into a valid claimset submission. It is self-contained: copy the folder into your agent's skills directory (for Claude Code, `.claude/skills/` in a project or `~/.claude/skills/`). Agents without skills support can be given its `SKILL.md` as instructions.

A `claimset-lab` skill for keeping a lab record while doing research will follow with the lab tool.

## Contributing

- The specifications in `spec/` are the source of truth; the validator implements them and the tests check both.
- After changing the validator, a spec or an example, run `python3 tools/sync_skills.py` to update the copies bundled in the skills.
- Run the tests with `python3 -m unittest discover -s tests`.

## Licence

Code is licensed under the Apache License 2.0 (`LICENSE`). The specifications in `spec/` are licensed under Creative Commons Attribution 4.0 International (CC BY 4.0).
