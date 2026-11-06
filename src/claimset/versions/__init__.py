"""Frozen validators of earlier specification versions (claimset spec §2.6).

A file is checked by the rules of the version it declares in `schema_version`. The current version's rules are
`claimset.validate`; when the specification moves on, `tools/freeze_validator.py` copies that module here as
`v<major>_<minor>.py` (`v0_37.py`) and lists it in FROZEN. A frozen validator is never edited: it is the
definition of its version's rules. Files of every version from the baseline (0.37) stay valid for good.
"""

import importlib

#: Versions with a frozen validator here, earlier than the current one. Filled by tools/freeze_validator.py.
FROZEN = ("0.37", "0.38",)


def frozen_validator(version):
    """The module that validates files of `version`, or None if there is no frozen validator for it."""
    if version not in FROZEN:
        return None
    major, minor = version.split(".")
    return importlib.import_module(f"{__name__}.v{major}_{minor}")
