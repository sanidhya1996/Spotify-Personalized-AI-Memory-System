"""Why this file exists
=====================

Runs the memory processor: reads accepted events off the queue and turns
them into stored memories.

    python scripts/run_processor.py           one pass, then stop
    python scripts/run_processor.py --forever keep going

abc.md:188 - "A memory processor classifies the event, extracts candidate
facts into a typed schema, resolves canonical content and concept
entities, and applies minimization and sensitivity rules."
abc.md:257 lists memory-processor as one of the six services.

Without this running, events are captured but never become memories. The
loop itself is in memory/worker.py, which can also run it inside the API on
a free host (RUN_WORKER_IN_API=true).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.worker import one_pass, run_forever  # noqa: E402,F401


if __name__ == "__main__":
    if "--forever" in sys.argv:
        print("processor running, Ctrl+C to stop")
        try:
            run_forever()
        except KeyboardInterrupt:
            print("stopped")
    else:
        one_pass()
