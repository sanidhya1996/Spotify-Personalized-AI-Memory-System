"""Why this file exists
=====================

Mark memories whose retention period has passed.

    python scripts/expire_memories.py

abc.md:118 requires expiry; abc.md:133 requires expired memories be
excluded from retrieval. Writing expires_at on a memory does nothing on
its own - something has to act on it.

Nodes are marked, never deleted, so audit history survives (abc.md:118 -
"without erasing audit history prematurely"). In production this runs on a
schedule.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import graph  # noqa: E402

if __name__ == "__main__":
    expired = graph.expire_memories()
    print(f"marked {expired} memory(ies) as expired")
