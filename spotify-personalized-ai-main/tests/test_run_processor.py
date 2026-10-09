"""Why this file exists
=====================

The worker runs unattended. If one pass throws - on Windows the Kafka
client's network thread sometimes dies with WinError 10038 - the worker
must log it and carry on, not exit. A dead worker looks exactly like a bug:
events are accepted and no memory ever appears.

These tests drive the loop with stand-in passes, so they need no stores.
"""

import importlib.util
from pathlib import Path

# The script is not a package module, so load it from its path.
_path = Path(__file__).resolve().parent.parent / "scripts" / "run_processor.py"
_spec = importlib.util.spec_from_file_location("run_processor", _path)
run_processor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_processor)

IDLE = {"handled": 0, "failed": 0, "memories_stored": 0}
BUSY = {"handled": 3, "failed": 0, "memories_stored": 2}


# A pass that throws is followed by another pass, not by an exit.
def test_a_failed_pass_does_not_stop_the_worker():
    outcomes = [OSError("[WinError 10038] not a socket"), BUSY, IDLE]
    calls = []

    def fake_pass():
        calls.append(1)
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    run_processor.run_forever(run_pass=fake_pass, sleep=lambda s: None,
                              max_passes=3)
    assert len(calls) == 3


# After a failure the worker waits before retrying, instead of spinning.
def test_it_waits_after_a_failure():
    sleeps = []

    def failing_pass():
        raise OSError("boom")

    run_processor.run_forever(run_pass=failing_pass, sleep=sleeps.append,
                              max_passes=2)
    assert sleeps == [5, 5]


# A busy pass goes straight into the next one; only an idle pass pauses.
def test_it_only_pauses_when_idle():
    outcomes = [BUSY, IDLE]
    sleeps = []
    run_processor.run_forever(run_pass=lambda: outcomes.pop(0),
                              sleep=sleeps.append, max_passes=2)
    assert sleeps == [3]
