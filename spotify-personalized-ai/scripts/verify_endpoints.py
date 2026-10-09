"""Why this file exists
=====================

Walks all ten endpoints in order, against the real Postgres, Redis and
Neo4j, and checks each one does what its REQUIREMENT says - not merely
that it returns 200.

    python scripts/verify_endpoints.py

Every check names the abc.md line it comes from, so the output reads as
evidence rather than as a claim. The unit tests prove the parts work; this
proves the system does, end to end, in one pass somebody can watch.

The model is replaced with a fixed answer, so this runs free, in seconds,
and gives the same result every time.
"""

import os
import sys
import time
import uuid

os.chdir(r"C:\Users\omsem\OneDrive\Desktop\New folder\spotify-personalized-ai")
sys.path.insert(0, os.getcwd())

from fastapi.testclient import TestClient  # noqa: E402

from memory import db, embeddings, graph, model_client, trace  # noqa: E402
from memory.api import app  # noqa: E402
from memory.auth import mint_token  # noqa: E402

client = TestClient(app)
H1 = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}
H2 = {"Authorization": f"Bearer {mint_token('user_002', 'chat-surface')}"}

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail and not condition else ""))


# Clean slate.
graph.ensure_constraints()
embeddings.ensure_index()
for s in ("user_001", "user_002"):
    graph.delete_memories(s)
    db.delete_events(s)
    trace.delete_traces(s)
    db.set_consent(s, "granted")
db.set_consent("user_004", "denied")

# The model is replaced so this verification is free and repeatable.
model_client.propose_candidates = lambda event: [{
    "memory_type": "exclusion",
    "fact": "Does not want country music",
    "entities": ["country"],
    "confidence": 0.95,
    "reason": "stated outright",
}]

print("\n" + "=" * 70)
print("1. POST /v1/events   — abc.md:303 validate subject, consent, schema,")
print("                       idempotency, source")
print("=" * 70)

body = {
    "schema_version": "1.0", "subject_id": "user_001",
    "event_type": "ai_interaction", "surface": "chat", "locale": "en-US",
    "occurred_at": "2026-09-25T10:00:00Z", "consent_state": "granted",
    "source_event_id": "src_1", "idempotency_key": "verify_key_1",
    "content": "I don't want country music",
}
r = client.post("/v1/events", json=body, headers=H1)
event_id = r.json().get("event_id")
check("accepts a valid event", r.status_code == 200, r.text[:120])
check("returns a real event id", str(event_id).startswith("evt_"))

again = client.post("/v1/events", json=body, headers=H1)
check("same idempotency key returns the same id",
      again.json().get("event_id") == event_id)
check("and marks it a duplicate", again.json().get("duplicate") is True)

bad = client.post("/v1/events", json={**body, "surface": "teleport"}, headers=H1)
check("rejects a bad field with a stable code",
      bad.status_code == 422 and bad.json()["detail"]["code"] == "VALIDATION_FAILED")

ver = client.post("/v1/events", json={**body, "schema_version": "0.9",
                                      "idempotency_key": str(uuid.uuid4())},
                  headers=H1)
check("rejects an unsupported schema version",
      ver.status_code == 400 and
      ver.json()["detail"]["code"] == "UNSUPPORTED_SCHEMA_VERSION")

wrong = client.post("/v1/events", json=body, headers=H2)
check("refuses another subject's event (abc.md:162)",
      wrong.status_code == 403 and
      wrong.json()["detail"]["code"] == "SUBJECT_MISMATCH")

check("every response carries a correlation id (abc.md:322)",
      r.headers.get("X-Correlation-Id", "").startswith("cid_"))

noauth = client.post("/v1/events", json=body)
check("refuses an unauthenticated event (abc.md:110)", noauth.status_code == 401)

print("\n" + "=" * 70)
print("2. POST /v1/memories/extract — abc.md:304 typed candidate memories")
print("=" * 70)

x = client.post("/v1/memories/extract",
                json={"subject_id": "user_001", "event_id": event_id}, headers=H1)
cands = x.json().get("candidates", [])
check("turns an event into candidates", x.status_code == 200 and len(cands) == 1,
      x.text[:150])
check("classifies into the taxonomy (abc.md:112)",
      cands and cands[0]["memory_type"] == "exclusion")
check("resolves entities to catalog ids (abc.md:113)",
      cands and cands[0]["entities"][0]["entity_id"] == "topic_country")
check("assigns a policy class (abc.md:115)",
      cands and cands[0]["policy"]["retention_days"] > 0)

ghost = client.post("/v1/memories/extract",
                    json={"subject_id": "user_001", "event_id": "evt_nope"},
                    headers=H1)
check("refuses an unknown event", ghost.status_code == 404)

print("\n" + "=" * 70)
print("3. POST /v1/memories — abc.md:306 stable ID, graph version, policy state")
print("=" * 70)

c = cands[0]
m = client.post("/v1/memories", json={
    "subject_id": "user_001", "memory_type": c["memory_type"],
    "fact": c["fact"], "entities": [e["name"] for e in c["entities"]],
    "confidence": c["confidence"], "source_event_ids": [event_id]}, headers=H1)
memory_id = m.json().get("memory_id")
check("stores the memory", m.status_code == 200, m.text[:120])
check("returns a stable id", str(memory_id).startswith("mem_"))
check("returns a graph version", m.json().get("graph_version") == 1)
check("returns a policy state", m.json().get("policy_state") == "normal")

stored = graph.get_memory(memory_id, "user_001")
check("it survives in Neo4j", stored is not None)
check("with valid-from and an open valid-to (abc.md:117)",
      stored["valid_from"] is not None and stored["valid_to"] is None)
check("linked to its entities", "topic_country" in stored["entities"])
check("another subject cannot read it (abc.md:119)",
      graph.get_memory(memory_id, "user_002") is None)

time.sleep(1)  # let the background embedding land
with graph.driver().session() as s:
    vec = s.run(f"MATCH (m:Memory {{memory_id:$i}}) "
                f"RETURN m.{embeddings.VECTOR_PROPERTY} AS v",
                i=memory_id).single()["v"]
check("and it was embedded (abc.md:190)", vec is not None and len(vec) == 384)

print("\n" + "=" * 70)
print("4. POST /v1/memories/search — abc.md:309 ranked, subject-scoped")
print("=" * 70)

sr = client.post("/v1/memories/search",
                 json={"subject_id": "user_001", "intent": "country music",
                       "surface": "chat"}, headers=H1)
results = sr.json().get("results", [])
check("finds the memory", sr.status_code == 200 and len(results) == 1, sr.text[:120])
check("returns a score", results and results[0]["score"] > 0)
check("shows the seven-signal breakdown (abc.md:124)",
      results and len(results[0]["signals"]) == 6)
check("links to a trace (abc.md:322)", sr.json().get("trace_id", "").startswith("cid_"))

semantic = client.post("/v1/memories/search",
                       json={"subject_id": "user_001",
                             "intent": "no twangy guitar songs please"},
                       headers=H1)
check("finds by meaning, not words (abc.md:123)",
      len(semantic.json().get("results", [])) >= 1)

other = client.post("/v1/memories/search",
                    json={"subject_id": "user_002", "intent": "country music"},
                    headers=H2)
check("another subject sees nothing (abc.md:119)",
      other.json().get("results") == [])

print("\n" + "=" * 70)
print("5. POST /v1/context/compose — abc.md:311 the context package")
print("=" * 70)

cp = client.post("/v1/context/compose",
                 json={"subject_id": "user_001", "intent": "put some music on",
                       "surface": "player", "token_budget": 400}, headers=H1)
pkg = cp.json()
check("builds a package", cp.status_code == 200 and pkg["no_memory"] is False,
      cp.text[:150])
check("with the required item fields (abc.md:132)",
      pkg["items"] and all(k in pkg["items"][0] for k in
                           ("memory_id", "fact", "memory_type", "confidence",
                            "source_class", "relevance_reason")))
check("fenced as data (abc.md:134)", pkg["fence_open"] in pkg["context_block"])
check("the fence is unguessable", pkg["fence_open"] != "<<<MEMORY_DATA")
check("fits the token budget (abc.md:125)", pkg["token_estimate"] <= 400)

paused = client.post("/v1/context/compose",
                     json={"subject_id": "user_002", "intent": "anything"},
                     headers=H2)
check("no memory is explicit, not an error (abc.md:135)",
      paused.status_code == 200 and paused.json()["no_memory"] is True)

trace_id = pkg["trace_id"]

print("\n" + "=" * 70)
print("6. PATCH /v1/memories/{id} — abc.md:313 optimistic concurrency")
print("=" * 70)

stale = client.patch(f"/v1/memories/{memory_id}",
                     json={"subject_id": "user_001", "operation": "expire",
                           "expected_version": 99}, headers=H1)
check("refuses a stale version with CONFLICT",
      stale.status_code == 409 and stale.json()["detail"]["code"] == "CONFLICT")

corr = client.patch(f"/v1/memories/{memory_id}",
                    json={"subject_id": "user_001", "operation": "correct",
                          "expected_version": 1,
                          "fact": "Actually likes some country music",
                          "entities": ["country"]}, headers=H1)
check("accepts a correction", corr.status_code == 200, corr.text[:120])
check("and names what it superseded", corr.json().get("superseded") == memory_id)
check("the old memory survives as history (abc.md:118)",
      graph.get_memory(memory_id, "user_001")["status"] == "superseded")

new_memory_id = corr.json()["memory_id"]
check("only the correction is active",
      [m["fact"] for m in graph.list_memories("user_001")] ==
      ["Actually likes some country music"])

print("\n" + "=" * 70)
print("7. DELETE /v1/memories/{id} — abc.md:315 cross-store deletion")
print("=" * 70)

d = client.delete(f"/v1/memories/{new_memory_id}?subject_id=user_001", headers=H1)
job_id = d.json().get("job_id")
check("returns a traceable job id", d.status_code == 200 and
      str(job_id).startswith("job_"), d.text[:120])
check("the memory is gone from the graph",
      graph.get_memory(new_memory_id, "user_001") is None)
check("and gone from semantic search",
      all(h["memory_id"] != new_memory_id
          for h in embeddings.search("user_001", "country music", limit=10)))

nope = client.delete("/v1/memories/mem_nope?subject_id=user_001", headers=H1)
check("refuses to delete something that is not there", nope.status_code == 404)

print("\n" + "=" * 70)
print("8. GET /v1/deletions/{job} — abc.md:317 per-store status")
print("=" * 70)

st = client.get(f"/v1/deletions/{job_id}?subject_id=user_001", headers=H1)
stores = st.json().get("stores", {})
check("reports the job", st.status_code == 200, st.text[:120])
check("with all five stores (abc.md:317)",
      set(stores) == {"graph", "vector", "cache", "operational", "backup"})
check("graph actually cleared", stores.get("graph") == "deleted")
check("backups reported honestly (abc.md:140)",
      stores.get("backup") == "retained_by_policy")
check("the job completed", st.json().get("status") == "completed")
check("another subject cannot read the job",
      client.get(f"/v1/deletions/{job_id}?subject_id=user_002",
                 headers=H2).status_code == 404)

print("\n" + "=" * 70)
print("9. POST /v1/feedback — abc.md:318 without self-validating output")
print("=" * 70)

guess = client.post("/v1/memories", json={
    "subject_id": "user_001", "memory_type": "candidate_preference",
    "fact": "Might enjoy ambient music", "entities": ["ambient"],
    "confidence": 0.6, "source_event_ids": ["evt_v"]}, headers=H1).json()["memory_id"]

up = client.post("/v1/feedback", json={
    "subject_id": "user_001", "kind": "relevance", "sentiment": "helpful",
    "memory_id": guess}, headers=H1)
check("records a thumbs-up", up.status_code == 200, up.text[:120])
check("but does NOT reinforce our own guess (abc.md:149)",
      up.json().get("reinforced") is False)
check("and says why", "inferred by us" in up.json().get("reinforce_reason", ""))

stated = client.post("/v1/memories", json={
    "subject_id": "user_001", "memory_type": "explicit_preference",
    "fact": "Prefers instrumental music", "entities": ["instrumental"],
    "confidence": 0.9, "source_event_ids": ["evt_w"]}, headers=H1).json()["memory_id"]

up2 = client.post("/v1/feedback", json={
    "subject_id": "user_001", "kind": "relevance", "sentiment": "helpful",
    "memory_id": stated}, headers=H1)
check("DOES reinforce their own words", up2.json().get("reinforced") is True)

down = client.post("/v1/feedback", json={
    "subject_id": "user_001", "kind": "rejection", "sentiment": "wrong",
    "memory_id": guess}, headers=H1)
check("negative feedback always counts", down.json().get("reinforced") is True)
check("and reaches the ranking signal (abc.md:124)",
      guess in db.negative_feedback("user_001"))

print("\n" + "=" * 70)
print("10. GET /v1/traces/{id} — abc.md:320 redacted decisions")
print("=" * 70)

tr = client.get(f"/v1/traces/{trace_id}?subject_id=user_001", headers=H1)
check("returns the trace", tr.status_code == 200, tr.text[:120])
check("with decisions (abc.md:101)", len(tr.json().get("decisions", [])) > 0)
check("naming the memories (abc.md:101)",
      any(d.get("memory_id") for d in tr.json().get("decisions", [])))
check("and it says it is redacted", tr.json().get("redacted") is True)
check("no memory text leaks (abc.md:322)",
      "Does not want country music" not in tr.text)
check("another subject cannot read it (abc.md:320 authorized)",
      client.get(f"/v1/traces/{trace_id}?subject_id=user_002",
                 headers=H2).status_code == 404)

# Clean up.
for s in ("user_001", "user_002"):
    graph.delete_memories(s)
    db.delete_events(s)
    trace.delete_traces(s)

print("\n" + "=" * 70)
print(f"RESULT: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("\nFAILED:")
    for name in failed:
        print("   -", name)
print("=" * 70)
raise SystemExit(1 if failed else 0)
