"""
Streamlit Cloud entry point.

Streamlit Cloud is configured to run this file at the repository root, but the
application itself lives in the `screener` package. This module is a launcher,
not a copy: importing `screener.dashboard` executes it, which is how a Streamlit
script renders.

Do not add application code here. The root of this repository previously held a
full duplicate of the package -- dashboard.py, data_fetcher.py, filters.py,
metrics.py, scoring.py and universe.py all existed twice and had to be hand-
synced. They drifted, and bug fixes applied to one copy went missing from the
other. There is now exactly one copy of each module, in `screener/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the repository root importable so `screener` resolves regardless of the
# working directory Streamlit happens to launch from.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Importing the module runs the app. The import is the entry point, so the
# unused-import warning here is expected.
import screener.dashboard  # noqa: F401,E402  (import has side effects by design)
