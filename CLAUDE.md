# Working on this repository

- `spec/` is normative. Change the spec first, then the validator, then the tests. A rule's number never changes once published; new rules get new numbers.
- The package uses only the Python standard library and supports Python 3.9+. Do not add dependencies without discussion.
- `skills/` bundles copies of the validator, specs and examples so each skill is self-contained. Never edit those copies; edit the source and run `python3 tools/sync_skills.py`. `tests/test_sync.py` fails if they drift.
- Every validator rule needs a test: a valid case that passes and a broken case that fails with that rule's number.
- Run `python3 -m unittest discover -s tests` before committing.
- Specification text is written for LLM readers as much as for humans: plain, explicit, one meaning per term.

## Pull requests

- Keep the description short: a bulleted list of what was done, each item one or two sentences. No other sections (no "Try it", "Next steps", background or test logs).
