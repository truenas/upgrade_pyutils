from __future__ import annotations

import os
import sys

# Make src/ importable for tests without installing the package.
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
