"""Allow `python -m claimset`."""

import sys

from .cli import main

sys.exit(main())
