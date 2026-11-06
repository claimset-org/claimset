# Compatibility files

Files of every specification version from the baseline (0.37), one folder per version. Every later validator must
accept them, each checked by its own version's rules (claimset spec §2.6; `tests/test_compat.py`). Files named
`published-*` are published files; the others are submissions.

Never edit or delete these files: they record what each version accepted. When the specification moves to a new
version, run `tools/freeze_validator.py` first, then add files of the new version in a new folder.
