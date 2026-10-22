# Fixture: adam-admissible-bounds

A real claimset and the lab record it was exported from, used to test the process checks
(validator warnings W17-W21 and rule 58; lab flag `reproduces_prompting_result` and the lab
tool's warnings). The files are copied unchanged.

| File | Source | sha256 |
|---|---|---|
| `claimset.json` | https://github.com/openforecast-org/smooth/blob/0e09b2be5637f5b889e30d977eb4131489f750c6/.claude/research/ADAM-admissible-bounds/claimset-ADAM-admissible-bounds.json | eaad2fa96dba2748ebe6264ece1fed42e13086c093f121f0d43358404d534b1a |
| `lab/lab.jsonl` | https://github.com/openforecast-org/smooth/blob/72c889476f3488f384c13122a7185458553a9231/.claude/lab/lab.jsonl (anchor commit of seq 916; the version the claimset's artefact A9 names) | 9751f4d4eebfeda0f442c2b6fc9caa0a4fddb1eb7609d3d87aafb57c9e88f8d0 |

Licence: CC-BY-4.0. Authors: Ivan Svetunkov (guarantor and supervisor) with Claude Code research
agents, in the smooth package repository (openforecast-org/smooth).

The claimset follows spec v0.18; tests change its `schema_version` in memory only. The lab's run
directories are not included, so tests that verify the record skip the run-file check.
