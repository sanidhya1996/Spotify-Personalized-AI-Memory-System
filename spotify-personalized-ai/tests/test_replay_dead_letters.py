"""Why this file exists
=====================

abc.md §5.4 - "dead-letter handling and idempotent replay for recoverable
ingestion failures."

Checks scripts/replay_dead_letters.py puts dead letters back on the main
queue and only then marks them done. The queue is replaced with a fake, so
the test never touches real failed events waiting on the real topic.
"""

import importlib.util
from pathlib import Path

from memory import queue

# The script is not a package module, so load it from its path.
_path = Path(__file__).resolve().parent.parent / "scripts" / "replay_dead_letters.py"
_spec = importlib.util.spec_from_file_location("replay_dead_letters", _path)
replay_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay_script)


class FakeMessage:
    def __init__(self, value):
        self.value = value


class FakeConsumer:
    """Iterates over given messages and remembers commits."""

    def __init__(self, messages):
        self.messages = [FakeMessage(m) for m in messages]
        self.commits = 0
        self.closed = False

    def __iter__(self):
        return iter(self.messages)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakeProducer:
    def flush(self):
        pass


DEAD = [
    {"event_id": "evt_1", "subject_id": "user_001", "correlation_id": "cid_1",
     "error": "ModelUnavailable"},
    {"event_id": "evt_2", "subject_id": "user_002", "correlation_id": "cid_2",
     "error": "ServiceUnavailable"},
]


# Wire the script to fakes; return the consumer and the list of republished events.
def fake_queue(monkeypatch, read):
    consumer = FakeConsumer(DEAD)
    published = []

    def fake_consumer(**kwargs):
        read.update(kwargs)
        return consumer

    monkeypatch.setattr(queue, "consumer", fake_consumer)
    monkeypatch.setattr(queue, "publish", lambda *args: published.append(args))
    monkeypatch.setattr(queue, "producer", lambda: FakeProducer())
    return consumer, published


def test_every_dead_letter_goes_back_on_the_main_queue(monkeypatch):
    read = {}
    consumer, published = fake_queue(monkeypatch, read)

    assert replay_script.replay() == 2
    # Only identifiers are republished - never content, never the old error.
    assert published == [("evt_1", "user_001", "cid_1"), ("evt_2", "user_002", "cid_2")]
    assert consumer.commits == 2 and consumer.closed
    assert read["topic"] == queue.DEAD_LETTER_TOPIC


def test_a_dry_run_counts_but_moves_nothing(monkeypatch):
    consumer, published = fake_queue(monkeypatch, {})

    assert replay_script.replay(dry_run=True) == 2
    assert published == [] and consumer.commits == 0
