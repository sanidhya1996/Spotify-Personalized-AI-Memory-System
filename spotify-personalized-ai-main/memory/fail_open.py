"""Why this file exists
=====================

If the memory stores are down, the listener must still get an answer - just
one without memory.

abc.md §5.5 Reliability - "Retrieval fails open to a non-personalized
response. No partial or cross-subject context after timeout or auth
failure."

Retrieval needs Neo4j (graph and vectors) and the embedding model. If either
fails, `retrieval.search` raises. Without this file that exception becomes a
500 error, and the AI surface asking for context gets nothing at all. With
it, the failure turns into an empty result and the composer answers "no
memory" - the same safe answer a listener with no history gets.

"Fails open" here means: open to a normal, non-personalized response. It
never means "return whatever we managed to fetch" - a half-finished result
is thrown away, never used.

Where it is used
----------------
memory/api.py, `compose_context` (POST /v1/context/compose) - the call that
feeds the AI. It calls `search_or_nothing` instead of `retrieval.search`.

POST /v1/memories/search is left as it is on purpose: it is an operator and
tool API, where a clear error is more useful than a silent empty list.
"""

import logging

from memory import retrieval

logger = logging.getLogger(__name__)


# Run retrieval; if anything fails, return an empty result and say why.
def search_or_nothing(**search_args) -> tuple[dict, str | None]:
    """Returns (result, failure).

    failure is None when retrieval worked. Otherwise it is a short reason
    such as "ServiceUnavailable", and result is empty - nothing partial.
    """
    try:
        return retrieval.search(**search_args), None
    except Exception as exc:  # noqa: BLE001 - any store failure must fail open
        # Identifiers only in the log, never memory text (abc.md:322).
        logger.warning("retrieval failed, answering without memory: %s",
                       type(exc).__name__)
        return {"results": [], "removed": [], "considered": 0}, type(exc).__name__
