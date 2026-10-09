"""Delete raw events that are past their retention date.

    python scripts/cleanup_expired.py

abc.md:109 - "Separate raw event retention from memory retention; not
every event becomes a retrievable memory."

Storing expires_at is not enough on its own; something has to remove the
rows. In production this runs on a schedule. Memories are untouched -
they live on their own, longer clock.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import db  # noqa: E402

if __name__ == "__main__":
    removed = db.delete_expired_events()
    print(f"deleted {removed} expired raw event(s)")
