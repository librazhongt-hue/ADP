"""Ensures the repo root is on sys.path so ``examples.*`` (which is not part
of the installed ``adp`` package) can be imported from tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
