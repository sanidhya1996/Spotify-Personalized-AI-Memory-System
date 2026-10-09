"use client";

// Why this file exists
// ====================
//
// Screen 4 of 7. abc.md:342 - "Correction and deletion: Correct or remove
// eligible memories, show propagation status, and prevent silent partial
// completion."
//
// The last clause is the demanding one. Deleting a memory means clearing it from
// the graph, the vector index, the cache and the operational store, and a
// backup inside its retention window cannot be cleared at all. A screen that
// says "deleted" over a job where one store failed would be lying, so this one
// polls GET /v1/deletions/{job_id} until the job finishes and shows every store
// on its own line, with anything unfinished called out.
//
// Three endpoints, in order:
//   POST   /v1/memories/search      find the memory to act on
//   PATCH  /v1/memories/{id}        correct it, under optimistic concurrency
//   DELETE /v1/memories/{id}        start deletion, get a job id
//   GET    /v1/deletions/{job_id}   watch every store until it finishes

import { useEffect, useState } from "react";
import { ApiFailure, del, get, patch, post } from "@/lib/api";
import type {
  DeletionAccepted,
  DeletionStatus,
  MemoryUpdated,
  RankedMemory,
  SearchResult,
} from "@/lib/types";
import { useSubject } from "@/lib/useSubject";
import {
  Badge,
  Button,
  Card,
  ErrorNote,
  Field,
  inputClass,
  typeTone,
} from "@/components/ui";

// A store's state, in the words the backend uses, and what each one means.
const STORE_MEANING: Record<string, string> = {
  deleted: "cleared",
  nothing_to_delete: "nothing was there",
  retained_by_policy: "kept - inside its backup retention window",
  failed: "NOT cleared",
};

export default function CorrectionAndDeletionPage() {
  const subjectId = useSubject();

  const [query, setQuery] = useState("");
  const [memories, setMemories] = useState<RankedMemory[]>([]);
  const [chosen, setChosen] = useState<RankedMemory | null>(null);

  // A correction needs the new wording and the version last seen. The version
  // is what makes the write safe: if the memory moved on, the backend refuses.
  const [newFact, setNewFact] = useState("");
  const [version, setVersion] = useState(1);

  const [corrected, setCorrected] = useState<MemoryUpdated | null>(null);
  const [job, setJob] = useState<DeletionAccepted | null>(null);
  const [status, setStatus] = useState<DeletionStatus | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Find memories to act on. Chat is used because the policy registry allows
  // the most types there, so nothing eligible is hidden from an operator.
  async function find() {
    setBusy("find");
    setFailure(null);
    try {
      const found = await post<SearchResult>("/v1/memories/search", {
        subject_id: subjectId,
        intent: query.trim() || "everything this listener has told us",
        surface: "chat",
        limit: 50,
      });
      setMemories(found.results);
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    find();
    setChosen(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectId]);

  // Pick one to work on, and clear anything left from the last one.
  function choose(memory: RankedMemory) {
    setChosen(memory);
    setNewFact(memory.fact);
    setVersion(1);
    setCorrected(null);
    setJob(null);
    setStatus(null);
    setFailure(null);
  }

  // Correct it. abc.md:118 - this supersedes the old memory rather than
  // overwriting it, so the history survives and the correction is a new memory.
  async function correct() {
    if (!chosen) return;
    setBusy("correct");
    setFailure(null);
    try {
      const updated = await patch<MemoryUpdated>(`/v1/memories/${chosen.memory_id}`, {
        subject_id: subjectId,
        operation: "correct",
        expected_version: version,
        fact: newFact,
        entities: [],
        confidence: 1.0,
      });
      setCorrected(updated);
      // Everything after this points at the correction, not the old memory.
      setChosen({ ...chosen, memory_id: updated.memory_id, fact: newFact });
      setVersion(updated.graph_version);
      find();
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(null);
    }
  }

  // Expire it. abc.md:313 - closes the memory without replacing it.
  async function expire() {
    if (!chosen) return;
    setBusy("expire");
    setFailure(null);
    try {
      const updated = await patch<MemoryUpdated>(`/v1/memories/${chosen.memory_id}`, {
        subject_id: subjectId,
        operation: "expire",
        expected_version: version,
      });
      setCorrected(updated);
      setVersion(updated.graph_version);
      find();
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(null);
    }
  }

  // Start deletion and then watch it. The request returns a receipt, not a
  // result - the work crosses four stores afterwards.
  async function remove() {
    if (!chosen) return;
    setBusy("delete");
    setFailure(null);
    setStatus(null);
    try {
      const accepted = await del<DeletionAccepted>(
        `/v1/memories/${chosen.memory_id}?subject_id=${encodeURIComponent(subjectId)}`,
      );
      setJob(accepted);
      await watch(accepted.job_id);
      find();
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(null);
    }
  }

  // Poll until the job stops being in progress. This is what "prevent silent
  // partial completion" means in practice: never stop looking while it is
  // unfinished, and never summarise four stores as one word.
  async function watch(jobId: string) {
    for (let attempt = 0; attempt < 15; attempt += 1) {
      const next = await get<DeletionStatus>(
        `/v1/deletions/${jobId}?subject_id=${encodeURIComponent(subjectId)}`,
      );
      setStatus(next);
      if (next.status !== "pending" && next.status !== "in_progress") return;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  // Is every store accounted for? A store still pending, or one that failed,
  // means the deletion is not finished and the screen must not imply it is.
  const unfinished = Object.entries(status?.stores ?? {}).filter(
    ([, state]) => !["deleted", "nothing_to_delete", "retained_by_policy"].includes(state),
  );

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">Correction and deletion</h1>
        <p className="mt-1 text-sm text-muted">
          Correct, expire or remove a memory for{" "}
          <span className="font-mono text-ink">{subjectId}</span>, and watch the
          removal reach every store.
        </p>
      </header>

      <Card title="1 · Find the memory" hint="POST /v1/memories/search">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <Field label="Search" hint="Leave empty to list everything.">
              <input
                className={inputClass}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && find()}
              />
            </Field>
          </div>
          <Button onClick={find} disabled={busy === "find"}>
            {busy === "find" ? "Searching…" : "Search"}
          </Button>
        </div>

        <ul className="mt-3 flex flex-col gap-1">
          {memories.length === 0 && (
            <li className="text-sm text-muted">Nothing for this subject.</li>
          )}
          {memories.map((memory) => (
            <li key={memory.memory_id}>
              <button
                type="button"
                onClick={() => choose(memory)}
                className={`w-full rounded-lg border p-2 text-left transition ${
                  chosen?.memory_id === memory.memory_id
                    ? "border-accent/50 bg-raised"
                    : "border-edge bg-raised/40 hover:border-faint"
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={typeTone(memory.memory_type)}>{memory.memory_type}</Badge>
                  <span className="ml-auto font-mono text-[10px] text-faint">
                    {memory.memory_id}
                  </span>
                </div>
                <p className="mt-1 text-sm text-ink wrap-anywhere">{memory.fact}</p>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      {failure && (
        <ErrorNote
          code={failure.code}
          message={failure.message}
          correlationId={failure.correlationId}
        />
      )}

      {chosen && (
        <>
          <Card
            title="2 · Correct or expire"
            hint="PATCH /v1/memories/{memory_id} — under optimistic concurrency"
          >
            <p className="mb-3 text-xs text-muted">
              A correction supersedes the old memory rather than overwriting it,
              so the history survives and the correction becomes a new memory. An
              expiry just closes this one.
            </p>

            <div className="grid gap-3 sm:grid-cols-4">
              <div className="sm:col-span-3">
                <Field label="Corrected wording">
                  <input
                    className={inputClass}
                    value={newFact}
                    onChange={(event) => setNewFact(event.target.value)}
                  />
                </Field>
              </div>
              <Field
                label="Version last seen"
                hint="A stale value is refused with 409."
              >
                <input
                  className={inputClass}
                  type="number"
                  min={1}
                  value={version}
                  onChange={(event) => setVersion(Number(event.target.value))}
                />
              </Field>
            </div>

            <div className="mt-3 flex flex-wrap gap-3">
              <Button
                onClick={correct}
                disabled={busy !== null || !newFact.trim() || newFact === chosen.fact}
              >
                {busy === "correct" ? "Correcting…" : "Correct it"}
              </Button>
              <Button variant="ghost" onClick={expire} disabled={busy !== null}>
                {busy === "expire" ? "Expiring…" : "Expire it"}
              </Button>
            </div>

            {corrected && (
              <div className="mt-3 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="good">{corrected.status}</Badge>
                  <span className="font-mono text-xs text-muted">
                    {corrected.memory_id}
                  </span>
                  <span className="text-[11px] text-faint">
                    version {corrected.graph_version}
                  </span>
                </div>
                {corrected.superseded && (
                  <p className="mt-1 text-[11px] text-faint">
                    supersedes{" "}
                    <span className="font-mono">{corrected.superseded}</span> — the
                    old memory is closed, not erased
                  </p>
                )}
              </div>
            )}
          </Card>

          <Card
            title="3 · Remove it everywhere"
            hint="DELETE /v1/memories/{memory_id}, then GET /v1/deletions/{job_id}"
          >
            <p className="mb-3 text-xs text-muted">
              Deletion has to reach the graph, the vector index, the cache and the
              operational store. The request returns a job id; this screen then
              watches every store until the job finishes.
            </p>

            <Button onClick={remove} disabled={busy !== null}>
              {busy === "delete" ? "Deleting…" : "Delete this memory"}
            </Button>

            {job && (
              <p className="mt-3 font-mono text-[11px] text-faint">
                job {job.job_id}
              </p>
            )}

            {status && (
              <div className="mt-3 flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    tone={
                      unfinished.length > 0
                        ? "bad"
                        : status.status === "completed"
                          ? "good"
                          : "warn"
                    }
                  >
                    {status.status}
                  </Badge>
                  {status.completed_at && (
                    <span className="text-[11px] text-faint">
                      finished {new Date(status.completed_at).toLocaleTimeString()}
                    </span>
                  )}
                </div>

                <ul className="flex flex-col gap-1">
                  {Object.entries(status.stores).map(([store, state]) => (
                    <li
                      key={store}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-edge bg-raised px-3 py-1.5"
                    >
                      <span className="text-xs text-muted">{store}</span>
                      <span className="flex items-center gap-2">
                        <span className="text-[11px] text-faint">
                          {STORE_MEANING[state] ?? state}
                        </span>
                        <Badge
                          tone={
                            state === "deleted"
                              ? "good"
                              : state === "retained_by_policy"
                                ? "info"
                                : state === "nothing_to_delete"
                                  ? "neutral"
                                  : "bad"
                          }
                        >
                          {state}
                        </Badge>
                      </span>
                    </li>
                  ))}
                </ul>

                {/* The whole point of the screen: never let a partial deletion
                    look complete. */}
                {unfinished.length > 0 ? (
                  <div className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm">
                    <div className="font-semibold text-bad">
                      This deletion is NOT complete
                    </div>
                    <p className="mt-1 text-muted">
                      {unfinished.map(([store]) => store).join(", ")} did not
                      clear. The memory may still be retrievable. Do not report
                      this to the listener as deleted.
                    </p>
                    {status.error && (
                      <p className="mt-1 font-mono text-[11px] text-bad wrap-anywhere">
                        {status.error}
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-sm">
                    <div className="font-semibold text-accent">
                      Every store is accounted for
                    </div>
                    <p className="mt-1 text-muted">
                      Backups say <span className="font-mono">retained_by_policy</span>{" "}
                      rather than deleted. That is the honest answer for a backup
                      inside its retention window, not a failure.
                    </p>
                  </div>
                )}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
