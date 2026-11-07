# Compatibility files

Files of every specification version from the baseline, one folder per version. Every later validator must accept
them, each checked by its own version's rules (claimset spec §2.6; `tests/test_compat.py`). Files named
`published-*` are published files; the others are submissions.

The baseline is set when the platform launches (`BASELINE_VERSION` in `src/claimset/validate.py`). Until then only
the current version is accepted: there is one folder, for the current version, and when the specification moves on
it is replaced by a folder of the new version.

Once the baseline is set, never edit or delete these files: they record what each version accepted. When the
specification moves to a new version, run `tools/freeze_validator.py` first, then add files of the new version in a
new folder.
