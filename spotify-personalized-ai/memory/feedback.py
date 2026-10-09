"""Why this file exists
=====================

abc.md:318 - "Record relevance, correction, rejection, or experience
              feedback WITHOUT SELF-VALIDATING MODEL OUTPUT."
abc.md:149 - "Capture user corrections and reviewer decisions without
              automatically reinforcing model-generated claims."

A listener says a memory was helpful, or wrong, or irrelevant. Recording
that is easy. The requirement is about what we do with it.

The trap is a feedback loop that flatters the system. The model guesses
that somebody likes ambient music. The guess appears in a reply. The
listener clicks a thumbs-up on the reply. If that raises the guess's
confidence, the system has just used its own output as evidence for its
own output - and the guess gets more certain every time it is shown,
without a single new fact arriving.

So the rule here is asymmetric, and deliberately so:

    NEGATIVE feedback always counts.
        Being told we are wrong is information from the listener.

    POSITIVE feedback counts only for memories the listener stated
    themselves.
        A thumbs-up on our own guess is not evidence for the guess.

The asymmetry is not caution for its own sake. abc.md:57 - "one wrong
memory can be more damaging than three missing ones." Failing to
strengthen a correct guess costs little. Strengthening a wrong one
compounds.
"""

import uuid

from memory import db, graph

# Memory types that came from the listener's own words. Positive feedback
# on these is real evidence.
STATED_TYPES = ("explicit_preference", "exclusion", "correction")

# Memory types we produced ourselves. Positive feedback on these is not
# evidence of anything except that the reply read well.
INFERRED_TYPES = ("candidate_preference", "episode")


# Make an id for one piece of feedback.
def new_feedback_id() -> str:
    return f"fbk_{uuid.uuid4().hex[:16]}"


# Decide whether this feedback may change the memory's standing.
def may_reinforce(sentiment: str, memory_type: str | None) -> tuple[bool, str]:
    """abc.md:149 - never automatically reinforce a model-generated claim.

    Returns (allowed, reason). The reason is stored, so a later reviewer
    can see why a piece of feedback did or did not count.
    """
    if sentiment in ("unhelpful", "wrong"):
        # Being told we are wrong is always information.
        return True, "negative feedback always counts"

    if memory_type is None:
        return False, "no memory named, nothing to reinforce"

    if memory_type in INFERRED_TYPES:
        # The heart of the requirement.
        return False, (
            f"{memory_type} was inferred by us; positive feedback on our own "
            "output is not evidence for it"
        )

    return True, f"{memory_type} came from the listener's own words"


# Record one piece of feedback, and apply it only where allowed.
def record(subject_id: str, kind: str, sentiment: str,
           memory_id: str | None = None, trace_id: str | None = None) -> dict:
    memory_type = None
    if memory_id:
        memory = graph.get_memory(memory_id, subject_id)
        memory_type = (memory or {}).get("memory_type")

    reinforced, reason = may_reinforce(sentiment, memory_type)
    feedback_id = new_feedback_id()

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO feedback (feedback_id, subject_id, memory_id, trace_id,
                                  kind, sentiment, reinforced, reinforce_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (feedback_id, subject_id, memory_id, trace_id,
             kind, sentiment, reinforced, reason),
        )

    # Negative feedback is also written to the audit trail, because
    # retrieval reads it from there as a ranking signal (abc.md:124).
    if sentiment in ("unhelpful", "wrong") and memory_id:
        db.record_audit(
            action="feedback.negative",
            subject_id=subject_id,
            service_id="feedback-api",
            outcome=sentiment,
            correlation_id=trace_id or "",
            memory_id=memory_id,
        )

    return {
        "feedback_id": feedback_id,
        "recorded": True,
        "reinforced": reinforced,
        "reinforce_reason": reason,
    }


# Read one subject's feedback, newest first.
def list_for_subject(subject_id: str, limit: int = 50) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT feedback_id, memory_id, kind, sentiment,
                   reinforced, reinforce_reason, recorded_at
            FROM feedback WHERE subject_id = %s
            ORDER BY recorded_at DESC LIMIT %s
            """,
            (subject_id, limit),
        ).fetchall()

    keys = ("feedback_id", "memory_id", "kind", "sentiment",
            "reinforced", "reinforce_reason", "recorded_at")
    return [dict(zip(keys, row)) for row in rows]
