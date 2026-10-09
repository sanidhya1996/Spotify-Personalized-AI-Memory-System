"use client";

// Why this file exists
// ====================
//
// Screen 7 of 7. abc.md:345 - "Audit trace: Authorized view of tool calls,
// service decisions, memory identifiers, timestamps, and redacted outcomes."
//
// This is the screen used when a personalized response was wrong. It answers why
// a memory was or was not used, using identifiers and reasons only - the trace
// carries no memory text at all, which is exactly why an operator is allowed to
// read it (abc.md:320, "sensitive fields redacted").
//
// A trace id comes from any response: the context preview shows one, the search
// endpoint returns one, and every error body carries a correlation id that is
// the same identifier.

import { useState } from "react";
import { ApiFailure, get, post } from "@/lib/api";
import type { ContextPackage, TraceRecord } from "@/lib/types";
import { useSubject } from "@/lib/useSubject";
import { Badge, Button, Card, ErrorNote, Field, Stat, inputClass } from "@/components/ui";

// Which stage of the pipeline a decision came from, coloured so the shape of a
// trace is readable before any of it is read.
function stageTone(stage: string): "good" | "warn" | "bad" | "info" | "neutral" {
  if (stage === "ranking") return "info";
  if (stage === "policy") return "warn";
  if (stage === "composition") return "good";
  return "neutral";
}

// Whether a decision kept something or took it away.
function decisionTone(decision: string): "good" | "bad" | "neutral" {
  if (decision.startsWith("include")) return "good";
  if (decision.startsWith("exclude") || decision.startsWith("remove")) return "bad";
  return "neutral";
}

export default function AuditTracePage() {
  const subjectId = useSubject();

  const [traceId, setTraceId] = useState("");
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Look up a trace by id.
  async function load(id: string) {
    const wanted = id.trim();
    if (!wanted) return;
    setBusy("load");
    setFailure(null);
    setTrace(null);
    try {
      const found = await get<TraceRecord>(
        `/v1/traces/${encodeURIComponent(wanted)}?subject_id=${encodeURIComponent(subjectId)}`,
      );
      setTrace(found);
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(null);
    }
  }

  // Produce a trace to look at. Without this, an operator has to copy an id from
  // another screen before this one can show anything at all.
  async function makeOne() {
    setBusy("make");
    setFailure(null);
    try {
      const pack = await post<ContextPackage>("/v1/context/compose", {
        subject_id: subjectId,
        intent: "put some music on",
        surface: "player",
        token_budget: 500,
      });
      setTraceId(pack.trace_id);
      await load(pack.trace_id);
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(null);
    }
  }

  const included = (trace?.decisions ?? []).filter((d) =>
    d.decision.startsWith("include"),
  ).length;
  const excluded = (trace?.decisions ?? []).length - included;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">Audit trace</h1>
        <p className="mt-1 text-sm text-muted">
          Why a response used the memories it did. Identifiers, decisions and
          reasons — never memory text.
        </p>
      </header>

      <Card
        title="Look up a trace"
        hint="Any response carries one: the context preview shows it, and every error body has it as correlation_id."
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-72 flex-1">
            <Field label="Trace id">
              <input
                className={inputClass}
                placeholder="cid_…"
                value={traceId}
                onChange={(event) => setTraceId(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && load(traceId)}
              />
            </Field>
          </div>
          <Button onClick={() => load(traceId)} disabled={busy !== null || !traceId.trim()}>
            {busy === "load" ? "Loading…" : "Look up"}
          </Button>
          <Button variant="ghost" onClick={makeOne} disabled={busy !== null}>
            {busy === "make" ? "Composing…" : "Compose one now"}
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

      {trace && (
        <>
          <Card title="This trace" hint={trace.trace_id}>
            <div className="grid grid-cols-3 gap-2">
              <Stat label="decisions" value={trace.decisions.length} />
              <Stat label="memories kept" value={included} />
              <Stat label="memories dropped" value={excluded} />
            </div>
            <div className="mt-3">
              <Badge tone="info">
                {trace.redacted ? "redacted — carries no memory text" : "not redacted"}
              </Badge>
            </div>
          </Card>

          {/* Service decisions, with memory identifiers, scores and reasons.
              abc.md:345 asks for exactly these. */}
          <Card
            title="Service decisions"
            hint="Each stage of the pipeline, and what it decided about which memory."
          >
            {trace.decisions.length === 0 ? (
              <p className="text-sm text-muted">
                No decisions recorded against this id.
              </p>
            ) : (
              <ol className="flex flex-col gap-1">
                {trace.decisions.map((decision, index) => (
                  <li
                    key={index}
                    className="rounded-lg border border-edge bg-raised px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={stageTone(decision.stage)}>{decision.stage}</Badge>
                      <Badge tone={decisionTone(decision.decision)}>
                        {decision.decision}
                      </Badge>
                      {decision.memory_id && (
                        <span className="font-mono text-[11px] text-muted">
                          {decision.memory_id}
                        </span>
                      )}
                      {decision.score !== null && (
                        <span className="font-mono text-[11px] text-faint">
                          score {decision.score.toFixed(3)}
                        </span>
                      )}
                      {/* Timestamps - abc.md:345 */}
                      <span className="ml-auto text-[11px] text-faint">
                        {new Date(decision.recorded_at).toLocaleTimeString()}
                      </span>
                    </div>
                    {decision.reason && (
                      <p className="mt-1 text-[11px] text-faint wrap-anywhere">
                        {decision.reason}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </Card>

          {/* Tool calls - abc.md:345. These are the audited service actions:
              which service did what, to which subject, with what outcome. */}
          <Card
            title="Audited actions"
            hint="What the services did under this correlation id."
          >
            {trace.actions.length === 0 ? (
              <p className="text-sm text-muted">No audited actions under this id.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {trace.actions.map((action, index) => (
                  <li
                    key={index}
                    className="rounded-lg border border-edge bg-raised px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                      {Object.entries(action).map(([field, value]) => (
                        <span key={field} className="text-faint">
                          <span className="text-muted">{field}</span>{" "}
                          <span className="font-mono text-ink">
                            {value === null ? "—" : String(value)}
                          </span>
                        </span>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
