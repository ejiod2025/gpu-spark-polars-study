"""The synthetic campaign trees live in paper3_analysis/synthetic.py (they are part of the analysis code that design 5.4 hashes: the synthetic recovery run
uses them). This module keeps the name the tests import, and `python tests/paper3_tree_fakes.py --tree DIR` writes the standard four-host tree."""
from __future__ import annotations

from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from paper3_analysis.synthetic import *  # noqa: E402,F401,F403
from paper3_analysis.synthetic import main  # noqa: E402

if __name__ == "__main__":
    main()
