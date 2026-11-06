# Working on this repository

- `spec/` is normative. Change the spec first, then the validator, then the tests. A rule's number never changes once published; new rules get new numbers.
- Backward compatibility is essential (claimset spec §2.6). Every file valid under a specification version from the baseline (0.37) stays valid under it for good. Before raising `SCHEMA_VERSION`, run `python3 tools/freeze_validator.py`; never edit a frozen validator (`src/claimset/versions/`) or the files in `tests/compat/`. A new version may add optional fields and loosen rules; new required fields and stricter rules apply only to files of the new version. Lab records follow the same principle: a rule applies only to records begun under the version that introduced it.
- The format is a core with extensions (spec §2.7). What only one kind of work needs goes in an extension (`spec/extensions/`, `src/claimset/extensions/`), never in the core; what two extensions would both define goes in the core. A released extension version's module is never edited: a new version is a new module.
- The claimset specification is the vocabulary of the lab record and the revision specification. A list, form or meaning they share with a claimset is defined once, in the claimset specification, and named there by section; in code it lives in `validate.py` and the lab imports it. Never copy one.
- The package uses only the Python standard library and supports Python 3.10+. Do not add dependencies without discussion.
- `skills/` bundles copies of the validator, specs and examples so each skill is self-contained. Never edit those copies; edit the source and run `python3 tools/sync_skills.py`. `tests/test_sync.py` fails if they drift.
- Every validator rule needs a test: a valid case that passes and a broken case that fails with that rule's number.
- Run `python3 -m unittest discover -s tests` before committing.
- Specification text is written for LLM readers as much as for humans: plain, explicit, one meaning per term.

## Pull requests

- Keep the description short: a bulleted list of what was done, each item one or two sentences. No other sections (no "Try it", "Next steps", background or test logs).
