from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"

for path in (REPO_ROOT, TOOLS_DIR):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)
