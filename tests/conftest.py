"""Shared test setup. Puts the repo root on sys.path so `import codecoach` works with no install.

Test file names must be unique across tests/ (there are no __init__.py files in tests/, and the repo
root will get an __init__.py): prefix files by area, e.g. tests/core/test_core_models.py.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
