"""Why this file exists
=====================

The long-running worker keeps ONE Kafka consumer open (memory/worker.py,
processor.poll_once): joining the consumer group is slow on a hosted Kafka,
and a worker that re-joined every pass processed almost nothing. These
check it reuses the consumer, reopens it after a failure, and handles and
commits each message.
"""

import pytest

from memory import processor, queue, worker


class FakeMessage:
    def __init__(self, value, offset=0):
        self.value = value
        self.topic, self.partition, self.offset = "interaction-events", 0, offset


class FakeConsumer:
    def __init__(self, batches=None, fail=False):
        self.batches = list(batches or [])
        self.fail = fail
        self.commits = 0
        self.committed = []
        self.closed = False

    def poll(self, timeout_ms, max_records):
        if self.fail:
            raise ConnectionError("broker went away")
        return {"partition-0": self.batches.pop(0)} if self.batches else {}

    def commit(self, offsets=None):
        self.commits += 1
        self.committed.append(offsets)

    def close(self):
        self.closed = True


def test_poll_once_handles_and_commits_every_message(monkeypatch):
    seen = []
    monkeypatch.setattr(processor, "handle_message",
                        lambda body: seen.append(body["event_id"]) or
                        {"handled": 1, "failed": 0, "stored": 2})
    consumer = FakeConsumer([[FakeMessage({"event_id": "e1"}, offset=7),
                               FakeMessage({"event_id": "e2"}, offset=8)]])

    totals = processor.poll_once(consumer, wait_seconds=0)

    assert seen == ["e1", "e2"] and consumer.commits == 2
    # Each commit marks only that message done (next offset), never the batch.
    assert [list(c.values())[0].offset for c in consumer.committed] == [8, 9]
    assert totals == {"handled": 2, "failed": 0, "memories_stored": 4}


def test_the_worker_reuses_one_consumer(monkeypatch):
    opened = []
    monkeypatch.setattr(queue, "consumer", lambda: opened.append(FakeConsumer()) or opened[-1])

    one_pass = worker._OpenConsumerPass()
    for _ in range(3):
        one_pass()

    assert len(opened) == 1


def test_a_failed_pass_opens_a_fresh_consumer(monkeypatch):
    opened = []
    monkeypatch.setattr(queue, "consumer",
                        lambda: opened.append(FakeConsumer(fail=not opened)) or opened[-1])

    one_pass = worker._OpenConsumerPass()
    with pytest.raises(ConnectionError):
        one_pass()
    assert opened[0].closed

    one_pass()
    assert len(opened) == 2
