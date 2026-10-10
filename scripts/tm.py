#!/usr/bin/env python3
"""Entry point of the shared automation. `python3 scripts/tm.py --help`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tmflow.cli import main  # noqa: E402

sys.exit(main())
