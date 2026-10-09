"""All ten API endpoints, on one app and one port.

Endpoints that are still stubs accept a request and return a placeholder.
The real logic gets filled in one endpoint at a time.

Every endpoint except /health requires a bearer token (abc.md:162: "Every
read and write must bind to authenticated subject and service identities").
"""

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from memory import (
    accounts,
    cache,
    composer,
    db,
    deletion,
    feedback as feedback_service,
    embeddings,
    entities as entity_resolver,
    errors,
    extraction,
    fail_open,
    graph,
    memory_types,
    model_client,
    monitoring,
    policy,
    queue,
    retention_rules,
    trace as trace_service,
    retrieval,
)
from memory.auth import TOKEN_LIFETIME, Caller, authenticate, bind_subject, mint_token
from memory.models import (
    ConsentRequest,
    LoginRequest,
    LoginResult,
    SignupRequest,
    ConsentState,
    SUPPORTED_SCHEMA_VERSION,
)
from memory.models import (
    SUPPORTED_SCHEMA_VERSION,
    Event,
    EventAccepted,
    ExtractRequest,
    ExtractionResult,
    CreateMemoryRequest,
    MemoryCreated,
    SearchRequest,
    SearchResult,
    ComposeRequest,
    ContextPackage,
    PatchMemoryRequest,
    MemoryUpdated,
    DeletionAccepted,
    DeletionStatus,
    FeedbackRequest,
    FeedbackRecorded,
    TraceRecord,
    TraceDecision,
)

# Create the tables, Neo4j constraints and vector index if they are missing,
# once, when the API starts - so a fresh deployment needs no manual step.
# See memory/startup.py.
@asynccontextmanager
async def lifespan(_app):
    from memory import startup

    startup.prepare_stores()
    yield


app = FastAPI(
    title="Spotify Personalized AI Memory System",
    version="0.1.0",
    lifespan=lifespan,
)

# The consoles run on a different origin from the API, so the browser
# needs to be told this is allowed. abc.md:254 - the product surfaces are
# Next.js apps; abc.md:243 deploys them separately from the backend.
#
# The allowed origins are listed rather than opened to "*", because the
# API carries bearer tokens and a wildcard would let any site on the
# internet call it with a listener's credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",     # memory-console, in development
        "http://localhost:3001",     # memory-controls, in development
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-Id"],
    # So the browser can read the trace id off the response.
    expose_headers=["X-Correlation-Id"],
)

# abc.md:322 - a tracking number on every request, a stable code on every
# error.
app.middleware("http")(errors.add_correlation_id)


# Time every request, so the retrieval SLO has something to report.
#
# abc.md:170 - "P95 retrieval and context composition should remain within a
# 250 ms service budget for the pilot." A percentile needs the individual
# durations, so each request writes one row and GET /metrics computes the
# percentiles from them.
#
# The write is best-effort: a metrics failure must never fail the request it
# was measuring.
@app.middleware("http")
async def record_request_latency(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000

    route = f"{request.method} {request.url.path}"
    try:
        db.record_latency(route, elapsed_ms, response.status_code)
    except Exception:  # noqa: BLE001 - measuring must not break the measured
        pass

    # Useful when watching a single call by hand.
    response.headers["X-Duration-Ms"] = f"{elapsed_ms:.1f}"
    return response
app.add_exception_handler(RequestValidationError, errors.handle_validation_error)
app.add_exception_handler(StarletteHTTPException, errors.handle_http_error)


@app.get("/health")
def health():
    """Is the app alive? Public on purpose - no data is exposed."""
    return {"status": "ok"}


@app.get("/health/stores")
def health_stores():
    """Is every store connected, and is the worker running?

    Public like /health, so a deployment can be checked from outside. Only
    "ok" or an error type per store - never an address or a credential.
    See memory/status.py.
    """
    from memory import status

    return status.summary()


@app.get("/metrics")
def metrics(caller: Caller = Depends(authenticate)):
    """Operational numbers for the Overview screen.

    abc.md:143 - monitor ingestion lag, write failures and policy
    rejection rate. abc.md:339 - the console Overview shows service
    health, ingestion lag and quality metrics.

    Counts only. No subject ids, no event content - this is an
    operational view, not a window into anyone's data.

    Write failures, cache effectiveness and policy rejection rate come from
    memory/monitoring.py.
    """
    return {**db.ingestion_metrics(), **monitoring.summary()}


# --- Write path -----------------------------------------------------------

@app.post("/v1/events", response_model=EventAccepted)
def create_event(
    event: Event,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """1. Accept an eligible interaction event.

    Validates subject, consent, schema, idempotency and source
    (abc.md:303), and rejects malformed, unauthenticated, out-of-policy and
    unsupported-version events before anything is stored (abc.md:110).
    """
    # The token must be authorized for the subject named in the body.
    bind_subject(caller, event.subject_id)

    def deny(status: int, code: str, message: str) -> HTTPException:
        """Refuse the event, and write down why.

        Written inline, not as a background task: raising an exception
        replaces the response, and background tasks attached to a response
        that never gets sent are discarded. A rejection is cheap and rare,
        so waiting for one small insert is fine.
        """
        db.record_audit(
            action="event.rejected",
            subject_id=event.subject_id,
            service_id=caller.service_id,
            outcome="rejected",
            correlation_id=errors.correlation_id.get(),
            reason=code,
        )
        return HTTPException(status_code=status, detail=errors.error(code, message))

    # One caller must not be able to flood us (abc.md:356).
    if cache.is_rate_limited(event.subject_id):
        raise deny(
            429,
            errors.RATE_LIMITED,
            f"more than {cache.RATE_LIMIT_PER_MINUTE} events in one minute",
        )

    # Unsupported contract versions are rejected before anything else.
    if event.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise deny(
            400,
            errors.UNSUPPORTED_SCHEMA_VERSION,
            f"schema_version {event.schema_version} is not supported; "
            f"this service accepts {SUPPORTED_SCHEMA_VERSION}",
        )

    # Consent: we check OUR record, not what the caller claims.
    #
    # abc.md:187 - the ingestion API "verifies ... consent state". The
    # event carries the surface's belief (abc.md:108); the database says
    # what is actually true. If they disagree, the database wins.
    consent = db.get_consent(event.subject_id)

    if consent is None:
        # No record at all. We do not assume permission we never got.
        raise deny(403, errors.CONSENT_DENIED, "no consent record for this subject")

    if consent != "granted":
        raise deny(403, errors.CONSENT_DENIED, f"consent is {consent}")

    # Same key sent twice by the same subject: return the first event_id
    # and store nothing new. Kept in Redis so it expires after 24 hours
    # (abc.md:222) instead of growing forever.
    seen = cache.get_event_id(event.subject_id, event.idempotency_key)
    if seen is not None:
        background.add_task(
            db.record_audit,
            action="event.duplicate",
            subject_id=event.subject_id,
            service_id=caller.service_id,
            outcome="duplicate",
            correlation_id=errors.correlation_id.get(),
            event_id=seen,
        )
        return EventAccepted(event_id=seen, duplicate=True)

    event_id = f"evt_{uuid.uuid4().hex[:12]}"

    # Postgres first, then Redis. In that order a crash in between means a
    # stored event whose key is forgotten - a retry writes a second row,
    # which is recoverable. The other order would lose the event entirely.
    db.save_event(event_id, event.model_dump(mode="json"), caller.service_id)
    cache.remember(event.subject_id, event.idempotency_key, event_id)

    # abc.md:187 - "Accepted events enter a durable queue so graph
    # processing does not block the experience." The memory processor
    # picks it up from there; the listener does not wait for any of it.
    background.add_task(
        queue.publish,
        event_id,
        event.subject_id,
        errors.correlation_id.get(),
    )

    # The audit line is written after the reply is sent, so the caller does
    # not wait for it (abc.md:324 - "Keep the user path independent of
    # downstream graph-write latency").
    background.add_task(
        db.record_audit,
        action="event.accepted",
        subject_id=event.subject_id,
        service_id=caller.service_id,
        outcome="accepted",
        correlation_id=errors.correlation_id.get(),
        event_id=event_id,
    )

    return EventAccepted(event_id=event_id)


@app.post("/v1/memories/extract", response_model=ExtractionResult)
def extract_memories(
    request: ExtractRequest,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """2. Turn an approved event into typed candidate memories.

    abc.md:304 - "Convert an approved event into typed candidate memories
    for deterministic validation."

    The model reads the language; memory/extraction.py decides what is
    acceptable (abc.md:296 - never treat extraction as authoritative).
    """
    bind_subject(caller, request.subject_id)

    def deny(status: int, code: str, message: str) -> HTTPException:
        db.record_audit(
            action="extract.rejected",
            subject_id=request.subject_id,
            service_id=caller.service_id,
            outcome="rejected",
            correlation_id=errors.correlation_id.get(),
            reason=code,
            event_id=request.event_id,
        )
        return HTTPException(status_code=status, detail=errors.error(code, message))

    if cache.is_rate_limited(request.subject_id):
        raise deny(429, errors.RATE_LIMITED, "too many requests this minute")

    # Consent can change between capture and extraction, so it is checked
    # again here - abc.md:53 wants it enforced "before memory reaches
    # retrieval", not only at the door.
    consent = db.get_consent(request.subject_id)
    if consent != "granted":
        raise deny(403, errors.CONSENT_DENIED, f"consent is {consent}")

    # Subject-scoped read: knowing an event id is not enough (abc.md:110).
    event = db.get_event(request.event_id, request.subject_id)
    if event is None:
        raise deny(404, errors.NOT_FOUND, "no such event for this subject")

    try:
        proposals = model_client.propose_candidates(event)
    except model_client.ModelUnavailable as exc:
        # abc.md:158 - the experience degrades gracefully rather than
        # failing. No memory is invented when the model cannot be reached.
        raise deny(503, errors.SERVICE_UNAVAILABLE, str(exc)) from exc

    result = extraction.extract(event, proposals)

    background.add_task(
        db.record_audit,
        action="extract.completed",
        subject_id=request.subject_id,
        service_id=caller.service_id,
        outcome="no_memory" if result.no_memory else "extracted",
        correlation_id=errors.correlation_id.get(),
        event_id=request.event_id,
    )

    return result


@app.post("/v1/memories", response_model=MemoryCreated)
def create_memory(
    request: CreateMemoryRequest,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """3. Create an explicit or approved memory, and store it in the graph.

    abc.md:306 - "Create an explicit or approved memory and return stable
    ID, graph version, and policy state."
    """
    bind_subject(caller, request.subject_id)

    # Refuse and record why.
    def deny(status: int, code: str, message: str) -> HTTPException:
        db.record_audit(
            action="memory.rejected",
            subject_id=request.subject_id,
            service_id=caller.service_id,
            outcome="rejected",
            correlation_id=errors.correlation_id.get(),
            reason=code,
        )
        return HTTPException(status_code=status, detail=errors.error(code, message))

    if cache.is_rate_limited(request.subject_id):
        raise deny(429, errors.RATE_LIMITED, "too many requests this minute")

    consent = db.get_consent(request.subject_id)
    if consent != "granted":
        raise deny(403, errors.CONSENT_DENIED, f"consent is {consent}")

    # The same sensitivity rule as extraction. A memory written directly
    # through this endpoint must not bypass abc.md:53.
    if extraction.looks_sensitive(request.fact):
        raise deny(403, errors.CONSENT_DENIED, "sensitive inference is not storable")

    # Resolve names to catalog ids (abc.md:113) and stamp the policy class
    # from the registry (abc.md:115). Neither is taken from the caller.
    resolved = entity_resolver.resolve_all(request.entities)
    policy_class = policy.classify(request.memory_type, subject_id=request.subject_id)

    candidate = {
        "memory_type": request.memory_type,
        "fact": request.fact,
        "confidence": request.confidence,
        "entities": [e.model_dump() for e in resolved],
        "policy": policy_class.model_dump(mode="json"),
        "source_event_ids": request.source_event_ids,
        "evidence_count": max(1, len(request.source_event_ids)),
    }

    if request.supersedes:
        # The caller named the memory this replaces. abc.md:118 - close the
        # old fact, keep it as history.
        existing = graph.get_memory(request.supersedes, request.subject_id)
        if existing is None:
            raise deny(404, errors.NOT_FOUND, "no such memory for this subject")
        created = graph.supersede(request.supersedes, request.subject_id, candidate)

    else:
        # Nobody told us about a clash, so look for one ourselves.
        # abc.md:189 - "Contradictions close or supersede prior facts
        # instead of silently replacing history."
        entity_ids = [e.entity_id for e in resolved if e.entity_id]
        related = graph.find_about(request.subject_id, entity_ids)

        contradicted = next(
            (m for m in related
             if graph.contradicts(request.memory_type, m["memory_type"])),
            None,
        )
        same = next(
            (m for m in related if m["memory_type"] == request.memory_type),
            None,
        )

        if contradicted is not None:
            # "I don't want country" arriving after "I love country".
            created = graph.supersede(
                contradicted["memory_id"], request.subject_id, candidate
            )
        elif same is not None:
            # The same thing said again. abc.md:49 - repeated evidence
            # strengthens one memory rather than making a second.
            created = graph.strengthen(
                same["memory_id"], request.subject_id,
                request.source_event_ids, request.confidence,
            )
        else:
            created = graph.create_memory(request.subject_id, candidate)

    # abc.md:190 step 10 - embed the memory under the same id, right after
    # it is written. Done in the background so the caller does not wait for
    # the model to run.
    background.add_task(
        embeddings.store_for_memory,
        created["memory_id"],
        request.subject_id,
        request.fact,
    )

    background.add_task(
        db.record_audit,
        action="memory.created",
        subject_id=request.subject_id,
        service_id=caller.service_id,
        outcome="created",
        correlation_id=errors.correlation_id.get(),
        memory_id=created["memory_id"],
    )

    return MemoryCreated(
        memory_id=created["memory_id"],
        graph_version=created["graph_version"],
        policy_state=policy_class.sensitivity,
        superseded=created.get("superseded"),
    )


# --- Read path ------------------------------------------------------------

@app.post("/v1/memories/search", response_model=SearchResult)
def search_memories(
    request: SearchRequest,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """4. Return ranked, subject-scoped memories for the current intent.

    abc.md:309 - ranked for intent, surface, locale and token budget.
    Hybrid candidates (abc.md:123), reranked on seven signals
    (abc.md:124), then diversity and policy filters (abc.md:125, :192).
    """
    bind_subject(caller, request.subject_id)

    # Refuse and record why.
    def deny(status: int, code: str, message: str) -> HTTPException:
        db.record_audit(
            action="search.rejected",
            subject_id=request.subject_id,
            service_id=caller.service_id,
            outcome="rejected",
            correlation_id=errors.correlation_id.get(),
            reason=code,
        )
        return HTTPException(status_code=status, detail=errors.error(code, message))

    if cache.is_rate_limited(request.subject_id):
        raise deny(429, errors.RATE_LIMITED, "too many requests this minute")

    # abc.md:53 - consent is enforced "before memory reaches retrieval".
    # This is that point.
    consent = db.get_consent(request.subject_id)
    if consent != "granted":
        raise deny(403, errors.CONSENT_DENIED, f"consent is {consent}")

    # Anything the listener marked unhelpful counts against a memory
    # (abc.md:124, negative feedback).
    negative = db.negative_feedback(request.subject_id)

    found = retrieval.search(
        subject_id=request.subject_id,
        intent=request.intent,
        surface=request.surface,
        limit=request.limit,
        negative=negative,
    )

    # abc.md:322 - every response links to a trace. The correlation id is
    # that link, and it is already on the response header.
    trace_id = errors.correlation_id.get()

    # abc.md:101 - the trace has to be replayable, so the decisions are
    # written down as they are taken.
    background.add_task(
        trace_service.record_search,
        trace_id, request.subject_id, found["results"], found["removed"],
    )

    background.add_task(
        db.record_audit,
        action="search.completed",
        subject_id=request.subject_id,
        service_id=caller.service_id,
        outcome="found" if found["results"] else "empty",
        correlation_id=trace_id,
    )

    return SearchResult(**found, trace_id=trace_id)


@app.post("/v1/context/compose", response_model=ContextPackage)
def compose_context(
    request: ComposeRequest,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """5. Apply policy and build the context package for the AI orchestrator.

    abc.md:311 - the last step before a listener sees anything. Finds the
    relevant memories, drops what must not be used, fits the budget, and
    hands back a package whose memory text is fenced as data
    (abc.md:134).
    """
    bind_subject(caller, request.subject_id)
    trace_id = errors.correlation_id.get()

    # Refuse and record why.
    def deny(status: int, code: str, message: str) -> HTTPException:
        db.record_audit(
            action="compose.rejected",
            subject_id=request.subject_id,
            service_id=caller.service_id,
            outcome="rejected",
            correlation_id=trace_id,
            reason=code,
        )
        return HTTPException(status_code=status, detail=errors.error(code, message))

    if cache.is_rate_limited(request.subject_id):
        raise deny(429, errors.RATE_LIMITED, "too many requests this minute")

    # abc.md:53 - consent is enforced before memory reaches retrieval.
    # A paused listener gets the no-memory package, not an error: the
    # experience must carry on without memory (abc.md:158).
    consent = db.get_consent(request.subject_id)
    if consent != "granted":
        package = composer.compose([], request.surface, request.token_budget,
                                   trace_id, healthy=False)
        package.reason = f"consent is {consent}"
        # abc.md:143 - the fallback rate needs every fallback counted, and
        # the reason recorded, because consent-paused and graph-unhealthy are
        # very different problems behind the same rate.
        background.add_task(
            db.record_fallback, request.subject_id, True,
            f"consent_{consent}", trace_id,
        )
        return package

    # abc.md:146 - the memory-disabled arm of the experiment. Checked before
    # retrieval, because the baseline must not pay the cost of a search whose
    # result it will not use.
    if db.cohort_of(request.subject_id) == "memory_disabled":
        package = composer.compose([], request.surface, request.token_budget,
                                   trace_id, memory_disabled=True)
        background.add_task(
            db.record_fallback, request.subject_id, True,
            "memory_disabled_cohort", trace_id,
        )
        return package

    # abc.md §5.5 - if the stores are down, answer without memory rather
    # than fail. See memory/fail_open.py.
    found, failure = fail_open.search_or_nothing(
        subject_id=request.subject_id,
        intent=request.intent,
        surface=request.surface,
        limit=request.token_budget // 50,   # a rough ceiling; the budget decides
        negative=db.negative_feedback(request.subject_id),
    )

    package = composer.compose(
        memories=found["results"],
        surface=request.surface,
        token_budget=request.token_budget,
        trace_id=trace_id,
        healthy=failure is None,
    )
    if failure:
        package.reason = f"memory unavailable ({failure}) - answered without memory"
    # Anything retrieval dropped is part of the same story.
    package.removed = found["removed"] + package.removed

    # abc.md:101 - record the decisions so the trace can be replayed.
    background.add_task(
        trace_service.record_search,
        trace_id, request.subject_id, package.items, package.removed,
    )

    # abc.md:135 - "record which memories influenced each response".
    background.add_task(
        db.record_audit,
        action="compose.completed",
        subject_id=request.subject_id,
        service_id=caller.service_id,
        outcome="no_memory" if package.no_memory else "composed",
        correlation_id=trace_id,
        memory_id=",".join(i.memory_id for i in package.items) or None,
    )

    # Every composition is counted, fallback or not - a rate needs both
    # halves. abc.md:143.
    background.add_task(
        db.record_fallback,
        request.subject_id,
        package.no_memory,
        package.reason if package.no_memory else None,
        trace_id,
    )

    return package


# --- Policy registry -------------------------------------------------------

@app.get("/policy")
def policy_registry(caller: Caller = Depends(authenticate)):
    """The policy registry, for the schema and policy screen.

    abc.md:237 - the system needs a "policy registry describing allowed
    memory types, sensitivity, purposes, retention, geography, age-related
    handling, and retrieval eligibility."
    abc.md:343 - the schema and policy screen shows "version history,
    allowed fields, retention, sensitivity, and rollout state."

    The registry already existed as data/policy_registry.yaml. It simply had
    no way to be read, so the screen that must show it could not. This
    returns the same file the policy engine enforces, so a console can never
    display a retention rule that differs from the one being applied.

    Read-only, and operational rather than versioned like /v1: it describes
    the service's own configuration, the way /metrics and /health do.
    """
    return {
        # The one event contract version accepted. abc.md:110 - unsupported
        # versions are rejected before anything is stored.
        "event_schema_version": SUPPORTED_SCHEMA_VERSION,

        # Every memory type, with the three fields abc.md:292 requires.
        "memory_types": policy.registry(),

        # Definition, example and counterexample per type - abc.md §7.2
        # step 1. See memory/memory_types.py.
        "definitions": memory_types.definitions(),

        # Geography and age limits on retention - abc.md §5.4. See
        # memory/retention_rules.py.
        "retention_rules": retention_rules.rules(),

        # The surfaces a memory can be eligible on. abc.md:108 fixes these.
        "surfaces": ["chat", "player", "search"],

        # abc.md:343 asks for rollout state. There is one registry and it is
        # live; saying so is more useful than an invented staging flag.
        "rollout_state": "live",
    }


# --- Listener login --------------------------------------------------------
#
# A pilot stand-in for Spotify's login - see memory/accounts.py. These two are
# the only endpoints, besides /health, that need no token: they are how a
# listener gets one.

# Which service the listener app's passes are issued to.
LISTENER_SERVICE = "listener-app"


@app.post("/auth/signup", response_model=LoginResult)
def signup(request: SignupRequest):
    """Create a listener account and log straight in.

    The user id must be new - not an existing account, not an existing
    subject - so nobody can sign up as somebody who already exists. Signing
    up switches memory on (the consent record), stores region and age band
    if given, and returns a pass for this listener only.
    """
    trace_id = errors.correlation_id.get()

    if accounts.id_taken(request.subject_id):
        raise HTTPException(
            status_code=409,
            detail=errors.error(errors.CONFLICT, "that user id is already taken"),
        )

    accounts.create_account(request.subject_id, request.password)
    db.set_consent(request.subject_id, "granted")
    db.set_region_and_age(request.subject_id, request.region, request.age_band)

    # Never the password - only that an account was made.
    db.record_audit(
        action="account.created", subject_id=request.subject_id,
        service_id=LISTENER_SERVICE, outcome="created", correlation_id=trace_id,
    )
    return LoginResult(
        subject_id=request.subject_id,
        token=mint_token(request.subject_id, LISTENER_SERVICE),
        expires_in_seconds=int(TOKEN_LIFETIME.total_seconds()),
    )


@app.post("/auth/login", response_model=LoginResult)
def login(request: LoginRequest):
    """Check a user id and password, and return a pass for that listener.

    A wrong user id and a wrong password get the same answer, so the
    response never reveals which ids exist. Five wrong passwords pause
    logins for that id for 15 minutes.
    """
    trace_id = errors.correlation_id.get()
    subject_id = request.subject_id.strip().lower()

    if accounts.is_locked(subject_id):
        raise HTTPException(
            status_code=429,
            detail=errors.error(errors.RATE_LIMITED,
                                "too many wrong passwords - try again in 15 minutes"),
        )

    if not accounts.check_login(subject_id, request.password):
        accounts.record_failure(subject_id)
        db.record_audit(
            action="login.failed", subject_id=subject_id,
            service_id=LISTENER_SERVICE, outcome="rejected", correlation_id=trace_id,
        )
        raise HTTPException(
            status_code=401,
            detail=errors.error(errors.UNAUTHENTICATED, "wrong user id or password"),
        )

    accounts.clear_failures(subject_id)
    db.record_audit(
        action="login.succeeded", subject_id=subject_id,
        service_id=LISTENER_SERVICE, outcome="ok", correlation_id=trace_id,
    )
    return LoginResult(
        subject_id=subject_id,
        token=mint_token(subject_id, LISTENER_SERVICE),
        expires_in_seconds=int(TOKEN_LIFETIME.total_seconds()),
    )


# --- Consent: pause and opt out -------------------------------------------
#
# abc.md:136 - "Provide review, correction, deletion, pause, and opt-out
#              paths with clear state and propagation status."
# abc.md:51  - the Memory Control Experience lets a listener "review,
#              correct, remove, pause, or opt out of eligible memory
#              behavior."
#
# Section 7.3's API table lists ten endpoints and none of them changes
# consent, so pause and opt-out had no path. Section 5.4 requires those
# paths, so this fills the gap rather than adding a new capability: the
# consent table, the three states and the enforcement all existed already.

# What each consent state means, in one line for the listener.
CONSENT_MEANING = {
    "granted": "Memory is used to personalize your experience.",
    "paused": "Memory is kept but not used. Nothing is deleted, and your "
              "experience carries on without it.",
    "denied": "Memory is switched off. Nothing new is captured and nothing "
              "stored is used.",
    "not_set": "Memory has not been switched on yet. Nothing is captured "
               "until it is.",
}


@app.get("/v1/consent", response_model=ConsentState)
def get_consent_state(subject_id: str, caller: Caller = Depends(authenticate)):
    """Report this subject's consent state in plain words.

    abc.md:136 asks for "clear state", which a bare enum is not. The
    meaning travels with the state so every surface says the same thing.
    """
    bind_subject(caller, subject_id)
    # No record means nobody has switched memory on yet - and events are
    # refused until they do - so say that rather than claim "granted".
    state = db.get_consent(subject_id) or "not_set"
    return ConsentState(
        subject_id=subject_id,
        state=state,
        meaning=CONSENT_MEANING.get(state, state),
    )


@app.patch("/v1/consent", response_model=ConsentState)
def set_consent_state(
    request: ConsentRequest,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """Pause, resume, or opt out of memory.

    abc.md:53 - consent is enforced before memory reaches retrieval, so
    changing it here changes behaviour on the very next request: a paused or
    denied subject gets an explicit no-memory package rather than an error
    (abc.md:158).

    Nothing is deleted. abc.md:137 keeps pause and deletion separate: pausing
    stops memory being used, deleting removes it. A listener who wants their
    memories gone uses DELETE /v1/memories/{id}, which reports its own
    cross-store propagation.
    """
    bind_subject(caller, request.subject_id)
    trace_id = errors.correlation_id.get()

    previous = db.get_consent(request.subject_id) or "not_set"
    db.set_consent(request.subject_id, request.state)
    # Region and age band, when given, for memory/retention_rules.py.
    db.set_region_and_age(request.subject_id, request.region, request.age_band)

    # abc.md:322 - a consent change is exactly the kind of decision an audit
    # trail exists for. Written inline rather than as a background task so it
    # cannot be lost.
    db.record_audit(
        action="consent.changed",
        subject_id=request.subject_id,
        service_id=caller.service_id,
        outcome=request.state,
        correlation_id=trace_id,
        reason=f"from {previous}",
    )

    # A paused or denied subject must not be answered from a warm cache.
    # abc.md:141 - cache invalidation is part of honouring the change.
    background.add_task(cache.forget_subject, request.subject_id)

    return ConsentState(
        subject_id=request.subject_id,
        state=request.state,
        meaning=CONSENT_MEANING.get(request.state, request.state),
    )


# --- Golden-set quality runs ----------------------------------------------

@app.get("/quality/runs")
def quality_runs(caller: Caller = Depends(authenticate)):
    """Golden-set runs, for the quality review screen.

    abc.md:344 - "Golden-set runs, failure clusters, multilingual cases,
    contradiction cases, and side-by-side memory-enabled comparisons."
    abc.md:361 - release is blocked if provenance falls below threshold, so
    the scores are stored rather than printed and lost.

    Counts and scores only. A golden case names memory identifiers and
    categories, never anybody's stored text.
    """
    runs = db.golden_runs()
    return {
        "runs": runs,
        # The newest run's cases, so the screen can cluster failures without
        # a second request.
        "cases": db.golden_cases(runs[0]["run_id"]) if runs else [],
        # abc.md:344 - the memory-disabled arm the comparison needs.
        "experiment": db.experiment_status(),
    }


# --- Subjects --------------------------------------------------------------

@app.get("/subjects")
def list_subjects(caller: Caller = Depends(authenticate)):
    """Every subject with a consent record, for the console's subject picker.

    abc.md:340 - the console looks only at "approved support or test
    identities". A subject becomes one by having a consent record, which is
    created through PATCH /v1/consent, so the picker lists exactly those.

    Only the id, the consent state, when it was set and the experiment
    group - nothing else is kept about a person (abc.md:53).
    """
    return {"subjects": db.list_subjects()}


# --- Correction and deletion ---------------------------------------------

@app.patch("/v1/memories/{memory_id}", response_model=MemoryUpdated)
def update_memory(
    memory_id: str,
    request: PatchMemoryRequest,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """6. Correct, supersede, or expire an eligible memory.

    abc.md:313 - "under optimistic concurrency": the caller says which
    version they last saw, and the request is refused if the memory has
    changed since. Two people editing at once cannot silently overwrite
    each other.
    """
    bind_subject(caller, request.subject_id)
    trace_id = errors.correlation_id.get()

    # Refuse and record why.
    def deny(status: int, code: str, message: str) -> HTTPException:
        db.record_audit(
            action="memory.update_rejected",
            subject_id=request.subject_id,
            service_id=caller.service_id,
            outcome="rejected",
            correlation_id=trace_id,
            reason=code,
            memory_id=memory_id,
        )
        return HTTPException(status_code=status, detail=errors.error(code, message))

    if cache.is_rate_limited(request.subject_id):
        raise deny(429, errors.RATE_LIMITED, "too many requests this minute")

    # Subject-scoped read: a memory id alone is not enough.
    existing = graph.get_memory(memory_id, request.subject_id)
    if existing is None:
        raise deny(404, errors.NOT_FOUND, "no such memory for this subject")

    # abc.md:313 - optimistic concurrency. The memory changed under them.
    if existing["graph_version"] != request.expected_version:
        raise deny(
            409,
            errors.CONFLICT,
            f"memory is at version {existing['graph_version']}, "
            f"not {request.expected_version}",
        )

    if request.operation == "expire":
        updated = graph.expire_one(memory_id, request.subject_id)
        result = MemoryUpdated(
            memory_id=updated["memory_id"],
            graph_version=updated["graph_version"],
            status=updated["status"],
        )

    else:  # correct
        if not (request.fact or "").strip():
            raise deny(422, errors.VALIDATION_FAILED, "a correction needs a fact")

        # The same sensitivity block as everywhere else (abc.md:53).
        if extraction.looks_sensitive(request.fact):
            raise deny(403, errors.CONSENT_DENIED,
                       "sensitive inference is not storable")

        resolved = entity_resolver.resolve_all(request.entities)
        candidate = {
            "memory_type": "correction",
            "fact": request.fact,
            "confidence": request.confidence,
            "entities": [e.model_dump() for e in resolved],
            "policy": policy.classify("correction", subject_id=request.subject_id).model_dump(mode="json"),
            "source_event_ids": existing.get("source_event_ids", []),
            "evidence_count": 1,
        }

        # abc.md:118 - a correction supersedes; it never overwrites.
        created = graph.supersede(memory_id, request.subject_id, candidate)
        background.add_task(
            embeddings.store_for_memory,
            created["memory_id"], request.subject_id, request.fact,
        )
        result = MemoryUpdated(
            memory_id=created["memory_id"],
            graph_version=created["graph_version"],
            status="active",
            superseded=memory_id,
        )

    background.add_task(
        db.record_audit,
        action=f"memory.{request.operation}",
        subject_id=request.subject_id,
        service_id=caller.service_id,
        outcome="updated",
        correlation_id=trace_id,
        memory_id=result.memory_id,
    )
    return result


@app.delete("/v1/memories/{memory_id}", response_model=DeletionAccepted)
def delete_memory(
    memory_id: str,
    subject_id: str,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """7. Start cross-store deletion and return a traceable job id.

    abc.md:315 - the work happens afterwards; this returns the receipt.
    abc.md:97 - retrieval eligibility is revoked immediately, so the
    memory stops being usable before any store has actually cleared.
    """
    bind_subject(caller, subject_id)
    trace_id = errors.correlation_id.get()

    existing = graph.get_memory(memory_id, subject_id)
    if existing is None:
        db.record_audit(
            action="memory.delete_rejected", subject_id=subject_id,
            service_id=caller.service_id, outcome="rejected",
            correlation_id=trace_id, reason=errors.NOT_FOUND,
            memory_id=memory_id,
        )
        raise HTTPException(
            status_code=404,
            detail=errors.error(errors.NOT_FOUND,
                                "no such memory for this subject"),
        )

    # abc.md:97 - revoke first. Even if a store is slow, the memory is
    # already unusable.
    deletion.revoke_eligibility(memory_id, subject_id)

    job_id = deletion.create_job(subject_id, memory_id)

    # The stores are cleared after the reply. abc.md:322 - asynchronous
    # work returns a job state.
    background.add_task(deletion.run_job, job_id, subject_id, memory_id)

    background.add_task(
        db.record_audit,
        action="memory.deleted",
        subject_id=subject_id,
        service_id=caller.service_id,
        outcome="accepted",
        correlation_id=trace_id,
        memory_id=memory_id,
    )
    return DeletionAccepted(job_id=job_id, memory_id=memory_id)


@app.get("/v1/deletions/{job_id}", response_model=DeletionStatus)
def get_deletion(
    job_id: str, subject_id: str, caller: Caller = Depends(authenticate)
):
    """8. Report deletion status across every store.

    abc.md:317 - "Report graph, vector, cache, operational-store, and
    backup-policy status." One line per store, so a partial failure is
    visible rather than hidden behind a single flag.
    """
    bind_subject(caller, subject_id)

    job = deletion.get_job(job_id, subject_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=errors.error(errors.NOT_FOUND, "no such job for this subject"),
        )

    return DeletionStatus(
        job_id=job["job_id"],
        memory_id=job["memory_id"],
        status=job["status"],
        stores={
            "graph": job["graph_status"],
            "vector": job["vector_status"],
            "cache": job["cache_status"],
            "operational": job["operational_status"],
            "backup": job["backup_status"],
        },
        requested_at=job["requested_at"],
        completed_at=job["completed_at"],
        error=job["error"],
    )


# --- Feedback and explainability -----------------------------------------

@app.post("/v1/feedback", response_model=FeedbackRecorded)
def create_feedback(
    request: FeedbackRequest,
    background: BackgroundTasks,
    caller: Caller = Depends(authenticate),
):
    """9. Record relevance, correction, rejection or experience feedback.

    abc.md:318 - "without self-validating model output". Negative feedback
    always counts; positive feedback counts only for memories the listener
    stated themselves. A thumbs-up on our own guess is not evidence for
    the guess.
    """
    bind_subject(caller, request.subject_id)
    trace_id = errors.correlation_id.get()

    if cache.is_rate_limited(request.subject_id):
        raise HTTPException(
            status_code=429,
            detail=errors.error(errors.RATE_LIMITED,
                                "too many requests this minute"),
        )

    # A memory named in feedback must belong to this subject.
    if request.memory_id and graph.get_memory(
        request.memory_id, request.subject_id
    ) is None:
        raise HTTPException(
            status_code=404,
            detail=errors.error(errors.NOT_FOUND,
                                "no such memory for this subject"),
        )

    result = feedback_service.record(
        subject_id=request.subject_id,
        kind=request.kind,
        sentiment=request.sentiment,
        memory_id=request.memory_id,
        trace_id=request.trace_id or trace_id,
    )

    background.add_task(
        db.record_audit,
        action="feedback.recorded",
        subject_id=request.subject_id,
        service_id=caller.service_id,
        outcome=request.sentiment,
        correlation_id=trace_id,
        memory_id=request.memory_id,
    )
    return FeedbackRecorded(**result)


@app.get("/v1/traces/{trace_id}", response_model=TraceRecord)
def get_trace(
    trace_id: str, subject_id: str, caller: Caller = Depends(authenticate)
):
    """10. Return the retrieval and policy decisions behind a response.

    abc.md:320 - "authorized retrieval and policy decisions with sensitive
    fields redacted." The trace carries memory ids, scores and reasons, so
    a reviewer can see the shape of a decision without reading anybody's
    private memories.
    """
    bind_subject(caller, subject_id)

    decisions = trace_service.get_trace(trace_id, subject_id)
    actions = trace_service.get_audit_for_trace(trace_id, subject_id)

    if not decisions and not actions:
        raise HTTPException(
            status_code=404,
            detail=errors.error(errors.NOT_FOUND,
                                "no such trace for this subject"),
        )

    return TraceRecord(
        trace_id=trace_id,
        decisions=[TraceDecision(**d) for d in decisions],
        actions=actions,
    )
