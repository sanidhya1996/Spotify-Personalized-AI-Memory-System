"""Why this file exists
=====================

abc.md:320 - "Return authorized retrieval and policy decisions with
              sensitive fields redacted."
abc.md:101 - the trace "replays the retrieval trace, shows candidate
              scores and policy decisions, and links the output to source
              memories."
abc.md:345 - "Audit trace: Authorized view of tool calls, service
              decisions, memory identifiers, timestamps, and redacted
              outcomes."

Somebody asks "why did it say that?" Without a record of the decisions,
the honest answer is a shrug - and abc.md:101 makes answering it a product
requirement, not a nice-to-have.

So every request that retrieves or composes writes down what it decided:
which memories it considered, which it used, which it dropped, and why.

The word that shapes this file is REDACTED. A trace is read by support
staff investigating a complaint, and it must let them see the shape of a
decision without reading the listener's private memories. So it carries
memory IDs, scores and reasons - never the memory text itself.

abc.md:322 says the same thing about logs: "Logs contain identifiers and
outcomes, not raw private content."
"""

from memory import db

# What a trace may contain. Anything not on this list is not recorded, so
# private content cannot leak in by accident later.
ALLOWED_STAGES = ("retrieval", "ranking", "policy", "composition")


# Write down one decision taken while answering a request.
def record_decision(trace_id: str, subject_id: str, stage: str,
                    decision: str, memory_id: str | None = None,
                    reason: str | None = None, score: float | None = None) -> None:
    """Identifiers, outcomes and reasons only - never memory text.

    Called in the background, so recording a decision never slows the
    request that made it.
    """
    if stage not in ALLOWED_STAGES:
        return

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO trace_decision
                (trace_id, subject_id, stage, memory_id, decision, reason, score)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (trace_id, subject_id, stage, memory_id, decision, reason, score),
        )


# Write down a whole request's worth of decisions in one go.
def record_search(trace_id: str, subject_id: str, kept: list, removed: list) -> None:
    """Called after a search or a composition, in the background."""
    for item in kept:
        record_decision(
            trace_id, subject_id, "ranking", "included",
            memory_id=getattr(item, "memory_id", None),
            reason=_top_signal(item),
            score=getattr(item, "score", None),
        )

    for note in removed:
        # `removed` entries look like "mem_abc: reason it was dropped".
        memory_id, _, reason = str(note).partition(": ")
        record_decision(
            trace_id, subject_id, "policy", "excluded",
            memory_id=memory_id or None,
            reason=reason or str(note),
        )


# Which signal contributed most to this memory's score.
def _top_signal(item) -> str | None:
    signals = getattr(item, "signals", None)
    if not signals:
        return None
    name = max(signals.items(), key=lambda pair: pair[1])[0]
    return f"strongest signal: {name}"


# Read one trace back, but only if it belongs to this subject.
def get_trace(trace_id: str, subject_id: str) -> list[dict]:
    """abc.md:119 - subject isolation. A trace id alone is not enough.

    Returns the decisions in the order they were taken.
    """
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT stage, memory_id, decision, reason, score, recorded_at
            FROM trace_decision
            WHERE trace_id = %s AND subject_id = %s
            ORDER BY id
            """,
            (trace_id, subject_id),
        ).fetchall()

    keys = ("stage", "memory_id", "decision", "reason", "score", "recorded_at")
    return [dict(zip(keys, row)) for row in rows]


# Read the audit lines that share this trace id.
def get_audit_for_trace(trace_id: str, subject_id: str) -> list[dict]:
    """abc.md:345 - the trace shows "service decisions" too, not only
    retrieval ones."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT action, outcome, reason, memory_id, event_id, recorded_at
            FROM audit_log
            WHERE correlation_id = %s AND subject_id = %s
            ORDER BY id
            """,
            (trace_id, subject_id),
        ).fetchall()

    keys = ("action", "outcome", "reason", "memory_id", "event_id", "recorded_at")
    return [dict(zip(keys, row)) for row in rows]


# Remove one subject's traces. Used by deletion and by tests.
def delete_traces(subject_id: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM trace_decision WHERE subject_id = %s",
                     (subject_id,))
