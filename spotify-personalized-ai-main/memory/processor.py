"""Why this file exists
=====================

abc.md:188 - "Extract and normalize. A memory processor classifies the
              event, extracts candidate facts into a typed schema,
              resolves canonical content and concept entities, and applies
              minimization and sensitivity rules."
abc.md:257 - `memory-processor/` is one of the six services the
              specification lists.

This is the piece that makes the system automatic. Without it, a memory
only appears if somebody calls extract and then memories by hand - which
is fine for a demo and useless in production.

It reads an event id off the queue, runs the chain that was already built
for endpoints 2 and 3, and stores whatever survives. Nobody copies an id
anywhere: it came off the queue.

The whole chain is:

    event id from the queue
        -> read the event          (subject-scoped)
        -> check consent again     (it may have changed since capture)
        -> ask the model           (endpoint 2's work)
        -> apply our rules         (endpoint 2's work)
        -> store what survives     (endpoint 3's work)

Failures go to the dead-letter topic rather than being retried forever or
dropped (abc.md:144).
"""

import logging

from memory import db, embeddings, extraction, graph, model_client, policy, queue

logger = logging.getLogger("memory.processor")


# Turn one event into stored memories. Returns what happened.
def process_event(event_id: str, subject_id: str) -> dict:
    """The whole chain for a single event.

    Raises ModelUnavailable when the model cannot be reached, so the
    caller can dead-letter the message and try again later. Anything the
    model gets wrong is caught by our rules instead, and simply dropped.
    """
    # Subject-scoped read: the worker has no more privilege than a caller.
    event = db.get_event(event_id, subject_id)
    if event is None:
        return {"stored": 0, "reason": "event not found"}

    # abc.md:53 - consent is enforced before memory is created. It can be
    # withdrawn between capture and processing.
    if db.get_consent(subject_id) != "granted":
        return {"stored": 0, "reason": "consent withdrawn since capture"}

    # An event with no words in it has nothing to classify.
    if not (event.get("content") or "").strip():
        return {"stored": 0, "reason": "no content to classify"}

    proposals = model_client.propose_candidates(event)
    result = extraction.extract(event, proposals)

    if result.no_memory:
        return {"stored": 0, "reason": "no memory worth keeping",
                "rejected": result.rejected}

    stored = []
    for candidate in result.candidates:
        memory = store_candidate(subject_id, candidate, event_id)
        stored.append(memory["memory_id"])

    return {"stored": len(stored), "memory_ids": stored,
            "rejected": result.rejected}


# Write one approved candidate into the graph, the same way endpoint 3 does.
def store_candidate(subject_id: str, candidate, event_id: str) -> dict:
    """Contradiction, evidence and embedding, exactly as endpoint 3.

    The logic lives in graph.py, so the worker and the endpoint cannot
    drift apart - a memory written here is indistinguishable from one
    written through the API.
    """
    payload = {
        "memory_type": candidate.memory_type,
        "fact": candidate.fact,
        "confidence": candidate.confidence,
        "entities": [e.model_dump() for e in candidate.entities],
        "policy": policy.classify(candidate.memory_type, subject_id=subject_id).model_dump(mode="json"),
        "source_event_ids": candidate.source_event_ids or [event_id],
        "evidence_count": candidate.evidence_count,
    }

    entity_ids = [e.entity_id for e in candidate.entities if e.entity_id]
    related = graph.find_about(subject_id, entity_ids)

    clash = next(
        (m for m in related if graph.contradicts(candidate.memory_type, m["memory_type"])),
        None,
    )
    same = next(
        (m for m in related if m["memory_type"] == candidate.memory_type), None
    )

    if clash is not None:
        created = graph.supersede(clash["memory_id"], subject_id, payload)
    elif same is not None:
        created = graph.strengthen(
            same["memory_id"], subject_id, payload["source_event_ids"],
            candidate.confidence,
        )
    else:
        created = graph.create_memory(subject_id, payload)

    # abc.md:190 - embed under the same memory id.
    embeddings.store_for_memory(created["memory_id"], subject_id, candidate.fact)
    return created


# A short, secret-free reason for a failure, for the audit log and /metrics.
def failure_reason(exc: Exception) -> str:
    """The error type, plus the provider's status for a model failure
    ("ModelUnavailable: 503 UNAVAILABLE") - enough to know what to fix,
    never a key, an address or the listener's words."""
    reason = type(exc).__name__
    text = str(exc)
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return reason + ": timeout"
    if "invalid json" in lowered:
        return reason + ": invalid JSON"
    for status in ("400", "401", "403", "404", "429", "500", "503",
                   "API_KEY_INVALID", "PERMISSION_DENIED", "UNAVAILABLE",
                   "RESOURCE_EXHAUSTED", "NOT_FOUND", "INVALID_ARGUMENT"):
        if status in text:
            reason += f": {status}"
            break
    return reason


# Process one queued message: store its memories, or dead-letter it.
def handle_message(body: dict) -> dict:
    """Returns {"handled": 0|1, "failed": 0|1, "stored": n}. Never raises:
    one bad event must not stop the rest."""
    try:
        outcome = process_event(body["event_id"], body["subject_id"])
        db.record_audit(
            action="processor.completed",
            subject_id=body["subject_id"],
            service_id="memory-processor",
            outcome="stored" if outcome.get("stored") else "no_memory",
            correlation_id=body.get("correlation_id", ""),
            event_id=body["event_id"],
            # Why nothing was stored, e.g. "no memory worth keeping" - one of
            # process_event's own fixed reasons, never the listener's words.
            reason=None if outcome.get("stored") else outcome.get("reason"),
        )
        return {"handled": 1, "failed": 0, "stored": outcome.get("stored", 0)}
    except Exception as exc:  # noqa: BLE001 - one bad event must not stop the rest
        logger.exception("event_id=%s failed", body.get("event_id"))
        queue.publish_dead_letter(body, f"{type(exc).__name__}: {exc}")
        # Counted as a write failure by memory/monitoring.py. If the
        # database itself is what failed, this cannot be written - that
        # must not stop the worker either.
        try:
            db.record_audit(
                action="processor.failed",
                subject_id=body.get("subject_id", ""),
                service_id="memory-processor",
                outcome="failed",
                correlation_id=body.get("correlation_id", ""),
                event_id=body.get("event_id"),
                reason=failure_reason(exc),
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not audit the failure")
        return {"handled": 0, "failed": 1, "stored": 0}


# Read the queue and process what is on it, until it is empty.
def run_once(max_messages: int = 100) -> dict:
    """One pass over the queue, with its own short-lived consumer. Used by
    `python scripts/run_processor.py` (one pass) and by tests.

    A message is only marked done after it has been processed, so a crash
    means it is picked up again rather than lost.
    """
    consumer = queue.consumer()
    totals = {"handled": 0, "failed": 0, "memories_stored": 0}

    try:
        for message in consumer:
            result = handle_message(message.value)
            totals["handled"] += result["handled"]
            totals["failed"] += result["failed"]
            totals["memories_stored"] += result["stored"]

            # Mark done only now, after the work actually happened.
            consumer.commit()

            if totals["handled"] + totals["failed"] >= max_messages:
                break
    finally:
        consumer.close()

    return totals


# Process whatever an already-open consumer has waiting.
def poll_once(consumer, wait_seconds: float = 5, max_messages: int = 100) -> dict:
    """For the long-running worker (memory/worker.py), which keeps ONE
    consumer open instead of opening a new one every pass.

    Opening a consumer means joining the consumer group. With Redpanda in
    Docker that is instant; with a hosted Kafka over the internet it takes
    seconds, and a worker that re-joined every pass spent nearly all its
    time joining - the deployed worker processed 1 event in 8 minutes.
    """
    totals = {"handled": 0, "failed": 0, "memories_stored": 0}
    batches = consumer.poll(timeout_ms=int(wait_seconds * 1000), max_records=max_messages)
    for records in batches.values():
        for message in records:
            result = handle_message(message.value)
            totals["handled"] += result["handled"]
            totals["failed"] += result["failed"]
            totals["memories_stored"] += result["stored"]
            # Mark done only THIS message, now that its work happened. A
            # plain commit() would mark the whole fetched batch done, and a
            # crash mid-batch would lose the rest.
            from kafka.structs import OffsetAndMetadata, TopicPartition

            consumer.commit({
                TopicPartition(message.topic, message.partition):
                    OffsetAndMetadata(message.offset + 1, None),
            })
    return totals
