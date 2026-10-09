"use client";

// Why this file exists
// ====================
//
// Screen 2 of 7. abc.md:340 - "Subject-scoped memory explorer: Search only with
// approved support or test identities; show timeline, graph relationships,
// source type, confidence, and status."
//
// There is no box for a subject id here: the subject is always the logged-in
// user, taken from their checked pass by the gateway
// (app/api/backend/[...path]/route.ts), so nobody can look at anyone else.
//
// Of the five things it must show, the search endpoint returns three directly -
// graph relationships (the entities each memory is about), source type (the
// memory type) and confidence. Timeline and status need the recorded_at and
// status properties that POST /v1/memories/search does not return; that gap is
// stated on the screen rather than filled in.

import { useEffect, useState } from "react";
import { ApiFailure, post } from "@/lib/api";
import type { RankedMemory, SearchResult, Surface } from "@/lib/types";
import { useSubject } from "@/lib/useSubject";
import {
  Badge,
  Button,
  Card,
  ErrorNote,
  Field,
  Stat,
  inputClass,
  typeTone,
} from "@/components/ui";

// The five memory types abc.md:112 names, for the type filter.
const TYPES = [
  "explicit_preference",
  "exclusion",
  "correction",
  "candidate_preference",
  "episode",
] as const;

// Whether the listener said a memory outright or we inferred it. abc.md:132
// calls this the source class, and it is the distinction that matters most when
// judging whether a memory should have been used.
function sourceClass(memoryType: string): "stated" | "observed" {
  return ["explicit_preference", "exclusion", "correction"].includes(memoryType)
    ? "stated"
    : "observed";
}

// One timestamp as a short local date and time, or a dash when absent.
function when(value: string | null | undefined): string {
  if (!value) return "—";
  const at = new Date(value);
  return Number.isNaN(at.getTime())
    ? "—"
    : at.toLocaleString(undefined, {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
}

// A memory's status decides its colour: green while it is still true, amber
// once a correction replaced it, grey once it expired.
function statusTone(status: string): "good" | "warn" | "neutral" {
  if (status === "active") return "good";
  if (status === "superseded") return "warn";
  return "neutral";
}

export default function MemoryExplorerPage() {
  const subjectId = useSubject();

  const [query, setQuery] = useState("");
  const [surface, setSurface] = useState<Surface>("chat");
  const [typeFilter, setTypeFilter] = useState<string>("all");

  const [result, setResult] = useState<SearchResult | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);

  // Fetch this subject's memories. An empty box asks broadly, which is how the
  // explorer shows everything rather than only what matches a phrase.
  async function load(intent: string) {
    setBusy(true);
    setFailure(null);
    try {
      const next = await post<SearchResult>("/v1/memories/search", {
        subject_id: subjectId,
        intent: intent.trim() || "everything this listener has told us",
        // chat is the widest surface in the policy registry, so it hides the
        // fewest memories from an operator looking at the whole picture.
        surface,
        limit: 50,
      });
      setResult(next);
    } catch (error) {
      setFailure(error as ApiFailure);
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  // Load on arrival, and again whenever the subject or surface changes.
  useEffect(() => {
    load(query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectId, surface]);

  const shown: RankedMemory[] = (result?.results ?? []).filter(
    (memory) => typeFilter === "all" || memory.memory_type === typeFilter,
  );

  // How many of each type, so the shape of what we hold is visible at a glance.
  const counts = (result?.results ?? []).reduce<Record<string, number>>(
    (totals, memory) => {
      totals[memory.memory_type] = (totals[memory.memory_type] ?? 0) + 1;
      return totals;
    },
    {},
  );

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">Memory explorer</h1>
        <p className="mt-1 text-sm text-muted">
          Everything held for{" "}
          <span className="font-mono text-ink">{subjectId}</span> - you only
          ever see your own memories.
        </p>
      </header>

      <Card
        title="Find"
        hint="Leave the box empty to see everything you have told us."
      >
        <div className="grid gap-3 sm:grid-cols-4">
          <div className="sm:col-span-2">
            <Field label="Search by meaning" hint="Semantic, not keyword.">
              <input
                className={inputClass}
                placeholder="e.g. podcasts, or running music"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && load(query)}
              />
            </Field>
          </div>

          <Field label="Surface" hint="Policy hides some types per surface.">
            <select
              className={inputClass}
              value={surface}
              onChange={(event) => setSurface(event.target.value as Surface)}
            >
              <option value="chat">chat</option>
              <option value="player">player</option>
              <option value="search">search</option>
            </select>
          </Field>

          <Field label="Type">
            <select
              className={inputClass}
              value={typeFilter}
              onChange={(event) => setTypeFilter(event.target.value)}
            >
              <option value="all">all types</option>
              {TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="mt-3">
          <Button onClick={() => load(query)} disabled={busy}>
            {busy ? "Searching…" : "Search"}
          </Button>
        </div>
      </Card>

      {failure && (
        <ErrorNote
          code={failure.code}
          message={failure.message}
          correlationId={failure.correlationId}
        />
      )}

      {result && (
        <>
          <Card title="What this subject has" hint={`trace ${result.trace_id}`}>
            <div className="grid grid-cols-3 gap-2">
              <Stat label="considered in the graph" value={result.considered} />
              <Stat label="retrievable here" value={result.results.length} />
              <Stat label="hidden by policy" value={result.removed.length} />
            </div>

            {Object.keys(counts).length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(counts).map(([type, count]) => (
                  <Badge key={type} tone={typeTone(type)}>
                    {type} · {count}
                  </Badge>
                ))}
              </div>
            )}
          </Card>

          {/* What policy hides on this surface. Part of understanding what the
              subject has, not a separate concern. */}
          {result.removed.length > 0 && (
            <Card
              title="Held, but not retrievable on this surface"
              hint="The policy registry decides which types each surface may use."
            >
              <ul className="flex flex-col gap-1">
                {result.removed.map((line, index) => (
                  <li
                    key={index}
                    className="rounded-lg border border-warn/30 bg-warn/5 px-3 py-1.5 text-xs text-muted wrap-anywhere"
                  >
                    {line}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card title={`Memories (${shown.length})`}>
            {shown.length === 0 ? (
              <p className="text-sm text-muted">
                Nothing for this subject on this surface. Try the chat surface,
                which hides the fewest types.
              </p>
            ) : (
              <ol className="flex flex-col gap-2">
                {shown.map((memory) => (
                  <li
                    key={memory.memory_id}
                    className="rounded-lg border border-edge bg-raised p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Source type - abc.md:340 */}
                      <Badge tone={typeTone(memory.memory_type)}>
                        {memory.memory_type}
                      </Badge>
                      <Badge
                        tone={sourceClass(memory.memory_type) === "stated" ? "good" : "warn"}
                      >
                        {sourceClass(memory.memory_type)}
                      </Badge>
                      {/* Status - abc.md:340. active while the memory is
                          still true, superseded once a correction replaced
                          it, expired once its retention ran out. */}
                      <Badge tone={statusTone(memory.status)}>{memory.status}</Badge>
                      <span className="ml-auto font-mono text-[11px] text-faint">
                        {memory.memory_id}
                      </span>
                    </div>

                    <p className="mt-2 text-sm text-ink wrap-anywhere">{memory.fact}</p>

                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-faint">
                      {/* Confidence - abc.md:340 */}
                      <span>confidence {memory.confidence.toFixed(2)}</span>
                      <span>seen in {memory.evidence_count} event(s)</span>
                      <span>relevance {memory.score.toFixed(3)}</span>
                    </div>

                    {/* Timeline - abc.md:340. abc.md:117 stores valid-from,
                        valid-to and recorded-at on every memory; these are
                        those three, which is what places a memory in time. */}
                    <div className="mt-2 flex flex-wrap items-center gap-3 border-t border-edge pt-2 text-[11px] text-faint">
                      <span>
                        recorded{" "}
                        <span className="text-muted">{when(memory.recorded_at)}</span>
                      </span>
                      <span>
                        valid from{" "}
                        <span className="text-muted">{when(memory.valid_from)}</span>
                      </span>
                      <span>
                        {memory.valid_to ? (
                          <>
                            valid to{" "}
                            <span className="text-muted">{when(memory.valid_to)}</span>
                          </>
                        ) : (
                          <span className="text-muted">still true</span>
                        )}
                      </span>
                    </div>

                    {/* Graph relationships - abc.md:340. These are the
                        :ABOUT edges to canonical catalog entities. */}
                    {memory.entities.length > 0 && (
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        <span className="text-[11px] text-faint">about</span>
                        {memory.entities.map((entity) => (
                          <span
                            key={entity}
                            className="rounded border border-edge px-1.5 py-0.5 font-mono text-[10px] text-muted"
                          >
                            {entity}
                          </span>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </>
      )}

    </div>
  );
}
