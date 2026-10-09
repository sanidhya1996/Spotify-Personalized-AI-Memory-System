"""Why this file exists
=====================

Sends failed events back through the worker, once the problem that failed
them is fixed.

abc.md §5.4 Observability - "Provide dead-letter handling and idempotent
replay for recoverable ingestion failures."

Dead-letter handling already existed: when the worker cannot process an
event (the model is down, a store times out), memory/processor.py puts it on
the "interaction-events-dlq" topic instead of losing it. This script is the
replay half: it reads that topic and puts each event back on the main queue,
where the running worker picks it up as normal.

    python scripts/replay_dead_letters.py           replay everything waiting
    python scripts/replay_dead_letters.py --dry-run only count them

Why replaying is safe ("idempotent")
-------------------------------------
- Only identifiers are on the queue. The worker re-reads the event from
  Postgres, so a replay uses the same stored event, never a copy.
- An event deleted or expired since is skipped ("event not found").
- Consent is checked again, so a listener who opted out in the meantime is
  not processed.
- Running the same event twice does not create a second memory: the worker
  recognises the same statement and just keeps it (memory/processor.py).
- This script remembers where it stopped (its own consumer group), so
  running it twice does not replay the same failures twice.

Where it is used
----------------
By an operator, after fixing whatever made events fail - for example once
the Gemini key works again, or a store that was down is back. The Overview
screen's "write failures" number says when there is something to replay.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import queue  # noqa: E402

# Its own group, so it keeps its own place in the dead-letter topic.
REPLAY_GROUP = "dead-letter-replay"


# Move every waiting dead letter back onto the main queue.
def replay(dry_run: bool = False) -> int:
    # A hosted Kafka needs longer to join (queue.consumer); locally 3s is enough.
    wait = None if queue.connection_settings() else 3000
    dead = queue.consumer(group_id=REPLAY_GROUP, timeout_ms=wait,
                          topic=queue.DEAD_LETTER_TOPIC)
    count = 0
    try:
        for message in dead:
            body = message.value
            print(f"  {body.get('event_id')}  failed with: {body.get('error')}")
            if not dry_run:
                queue.publish(body["event_id"], body["subject_id"],
                              body.get("correlation_id", ""))
                # Marked done only after it is safely back on the main queue.
                queue.producer().flush()
                dead.commit()
            count += 1
    finally:
        dead.close()
    return count


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    n = replay(dry_run=dry)
    print(f"{'found' if dry else 'replayed'} {n} dead-lettered event(s)")
