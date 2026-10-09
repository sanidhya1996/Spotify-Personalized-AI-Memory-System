"use client";

// Why this file exists
// ====================
//
// Screen 1 of 7. abc.md:339 - "Overview: Service health, ingestion lag,
// retrieval SLO, fallback rate, quality metrics, experiment status, and
// deletion backlog."
//
// All seven are here. Health is its own endpoint; the other six come from
// GET /metrics, which reads them from the audit trail and from the tables
// migration 006 added.
//
// Nothing on this screen is estimated. Where a number has no samples yet it
// says so, because an operations console that guesses is worse than one that
// admits it has not measured anything.

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiFailure, get, getHealth } from "@/lib/api";
import type { Metrics } from "@/lib/types";
import { Badge, Card, ErrorNote, Stat } from "@/components/ui";

export default function OverviewPage() {
  const [health, setHealth] = useState<"checking" | "up" | "down">("checking");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);

  // Ask once on load, then every ten seconds, so an operator watching this
  // screen sees the numbers move rather than a frozen snapshot.
  useEffect(() => {
    let live = true;

    async function refresh() {
      try {
        await getHealth();
        if (live) setHealth("up");
      } catch {
        if (live) setHealth("down");
        return;
      }
      try {
        const next = await get<Metrics>("/metrics");
        if (live) {
          setMetrics(next);
          setFailure(null);
        }
      } catch (error) {
        if (live) setFailure(error as ApiFailure);
      }
    }

    refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, []);

  // Seconds into words. Seconds alone are unreadable past a minute.
  function asDuration(seconds: number | null): string {
    if (seconds === null) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
  }

  // A rate as a percentage, or a dash when nothing has happened yet.
  function asPercent(rate: number, samples: number): string {
    return samples === 0 ? "—" : `${(rate * 100).toFixed(1)}%`;
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">Overview</h1>
        <p className="mt-1 text-sm text-muted">
          The seven operational views of `abc.md:339`. Refreshes every ten
          seconds.
        </p>
      </header>

      {/* 1 - Service health. */}
      <Card title="Service health" hint="GET /health">
        {health === "checking" && <Badge>checking…</Badge>}
        {health === "up" && <Badge tone="good">memory service is up</Badge>}
        {health === "down" && (
          <div>
            <Badge tone="bad">memory service is down</Badge>
            <p className="mt-2 text-sm text-muted">
              Start the API, and the worker beside it:
            </p>
            <pre className="mt-2 rounded-lg border border-edge bg-base p-3 font-mono text-[11px] text-muted">
              {"python -m uvicorn memory.api:app --reload --port 8000\npython scripts/run_processor.py --forever"}
            </pre>
          </div>
        )}
      </Card>

      {failure && (
        <ErrorNote
          code={failure.code}
          message={failure.message}
          correlationId={failure.correlationId}
        />
      )}

      {metrics && (
        <>
          {/* 3 - Retrieval SLO. abc.md:170 sets the 250 ms P95 budget. */}
          <Card
            title="Retrieval SLO"
            hint="Search and context composition only — mixing the write path in would flatter the number."
            right={
              metrics.retrieval_slo.within_budget === null ? (
                <Badge>no samples yet</Badge>
              ) : metrics.retrieval_slo.within_budget ? (
                <Badge tone="good">within budget</Badge>
              ) : (
                <Badge tone="bad">over budget</Badge>
              )
            }
          >
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat
                label="P50"
                value={
                  metrics.retrieval_slo.p50_ms === null
                    ? "—"
                    : `${metrics.retrieval_slo.p50_ms}ms`
                }
              />
              <Stat
                label={`P95 of ${metrics.retrieval_slo.budget_ms}ms`}
                value={
                  metrics.retrieval_slo.p95_ms === null
                    ? "—"
                    : `${metrics.retrieval_slo.p95_ms}ms`
                }
              />
              <Stat
                label="P99"
                value={
                  metrics.retrieval_slo.p99_ms === null
                    ? "—"
                    : `${metrics.retrieval_slo.p99_ms}ms`
                }
              />
              <Stat label="requests measured" value={metrics.retrieval_slo.samples} />
            </div>

            {metrics.retrieval_slo.p95_ms !== null && (
              <div className="mt-3">
                <div className="h-2 w-full rounded-full bg-raised">
                  <div
                    className={`h-2 rounded-full ${
                      metrics.retrieval_slo.within_budget ? "bg-accent" : "bg-bad"
                    }`}
                    style={{
                      width: `${Math.min(
                        100,
                        (metrics.retrieval_slo.p95_ms / metrics.retrieval_slo.budget_ms) * 100,
                      )}%`,
                    }}
                  />
                </div>
                <p className="mt-1 text-[11px] text-faint">
                  P95 against the {metrics.retrieval_slo.budget_ms}ms pilot budget,
                  over the last {metrics.retrieval_slo.window_minutes} minutes
                </p>
              </div>
            )}
          </Card>

          {/* 2 - Ingestion lag, and the event counts beside it. */}
          <Card
            title="Ingestion"
            hint="Lag is how long ago the newest event arrived. If it grows, events have stopped coming in."
          >
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              <Stat
                label="lag"
                value={asDuration(metrics.ingestion_lag_seconds)}
              />
              <Stat label="accepted" value={metrics.events.accepted} />
              <Stat label="rejected" value={metrics.events.rejected} />
              <Stat label="duplicate" value={metrics.events.duplicate} />
              <Stat label="stored" value={metrics.events.stored} />
            </div>

            <div className="mt-4">
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="text-muted">Policy rejection rate</span>
                <span className="font-mono text-ink">
                  {(metrics.rejection_rate * 100).toFixed(1)}%
                </span>
              </div>
              <div className="h-2 w-full rounded-full bg-raised">
                <div
                  className={`h-2 rounded-full ${
                    metrics.rejection_rate > 0.3 ? "bg-warn" : "bg-accent"
                  }`}
                  style={{ width: `${Math.min(100, metrics.rejection_rate * 100)}%` }}
                />
              </div>
            </div>

            {Object.keys(metrics.rejections_by_reason).length > 0 && (
              <ul className="mt-3 flex flex-col gap-1">
                {Object.entries(metrics.rejections_by_reason)
                  .sort((a, b) => b[1] - a[1])
                  .map(([reason, count]) => (
                    <li
                      key={reason}
                      className="flex items-center justify-between rounded-lg border border-edge bg-raised px-3 py-1.5"
                    >
                      <span className="font-mono text-xs text-muted">{reason}</span>
                      <span className="text-sm font-semibold tabular-nums text-ink">
                        {count}
                      </span>
                    </li>
                  ))}
              </ul>
            )}
          </Card>

          {/* 4 - Fallback rate. A fallback is normal behaviour, not an error:
              abc.md:167 requires retrieval to fail open. */}
          <Card
            title="Fallback rate"
            hint="Requests answered without memory. Failing open is correct behaviour — the reasons are what make the rate actionable."
          >
            <div className="grid grid-cols-3 gap-2">
              <Stat
                label="fell back"
                value={asPercent(metrics.fallback.rate, metrics.fallback.requests)}
              />
              <Stat label="context requests" value={metrics.fallback.requests} />
              <Stat label="without memory" value={metrics.fallback.fell_back} />
            </div>

            {Object.keys(metrics.fallback.by_reason).length > 0 ? (
              <ul className="mt-3 flex flex-col gap-1">
                {Object.entries(metrics.fallback.by_reason)
                  .sort((a, b) => b[1] - a[1])
                  .map(([reason, count]) => (
                    <li
                      key={reason}
                      className="flex items-center justify-between rounded-lg border border-edge bg-raised px-3 py-1.5"
                    >
                      <span className="font-mono text-xs text-muted">{reason}</span>
                      <span className="text-sm font-semibold tabular-nums text-ink">
                        {count}
                      </span>
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="mt-3 text-[11px] text-faint">
                No fallbacks in the last {metrics.fallback.window_minutes} minutes.
              </p>
            )}
          </Card>

          {/* 7 - Deletion backlog. abc.md:361 blocks release outright if
              deletion propagation is incomplete, so age matters as much as
              count. */}
          <Card
            title="Deletion backlog"
            hint="Jobs that have not finished clearing every store."
            right={
              metrics.deletion_backlog.unfinished === 0 ? (
                <Badge tone="good">clear</Badge>
              ) : (
                <Badge tone="bad">{metrics.deletion_backlog.unfinished} unfinished</Badge>
              )
            }
          >
            <div className="grid grid-cols-3 gap-2">
              <Stat label="unfinished" value={metrics.deletion_backlog.unfinished} />
              <Stat label="completed" value={metrics.deletion_backlog.completed} />
              <Stat
                label="oldest unfinished"
                value={asDuration(metrics.deletion_backlog.oldest_pending_seconds)}
              />
            </div>
            {metrics.deletion_backlog.unfinished > 0 && (
              <p className="mt-3 text-[11px] text-bad">
                Release is blocked while deletion propagation is incomplete
                (abc.md:361).
              </p>
            )}
          </Card>

          {/* Write failures, cache and policy - abc.md §5.4 asks these to be
              monitored; counted by the backend's memory/monitoring.py. */}
          <Card
            title="Write failures, cache and policy"
            hint="Events the worker could not store, repeats the cache caught, and memories policy removed."
          >
            <div className="grid grid-cols-3 gap-2">
              <Stat
                label={`write failures of ${metrics.write_failures.processed}`}
                value={metrics.write_failures.failed}
              />
              <Stat
                label={`cache hit rate (${metrics.cache_effectiveness.hits} of ${metrics.cache_effectiveness.lookups})`}
                value={`${(metrics.cache_effectiveness.hit_rate * 100).toFixed(0)}%`}
              />
              <Stat
                label={`policy rejections (${metrics.policy_rejection.excluded} of ${metrics.policy_rejection.considered})`}
                value={`${(metrics.policy_rejection.rate * 100).toFixed(0)}%`}
              />
            </div>
            {metrics.write_failures.failed > 0 && (
              <p className="mt-3 text-[11px] text-faint">
                Failed events wait in the dead-letter queue. Once the cause is
                fixed, replay them with: python scripts/replay_dead_letters.py
              </p>
            )}
          </Card>

          {/* 6 - Experiment status. abc.md:146. */}
          <Card
            title="Experiment status"
            hint={metrics.experiment.experiment}
            right={
              metrics.experiment.has_baseline ? (
                <Badge tone="good">baseline exists</Badge>
              ) : (
                <Badge tone="warn">no baseline arm</Badge>
              )
            }
          >
            <div className="grid grid-cols-3 gap-2">
              <Stat label="memory enabled" value={metrics.experiment.memory_enabled} />
              <Stat label="memory disabled" value={metrics.experiment.memory_disabled} />
              <Stat label="subjects allocated" value={metrics.experiment.subjects} />
            </div>
            <p className="mt-3 text-[11px] text-faint">
              A subject in the memory-disabled arm is answered with an explicit
              no-memory package, taking the same path as any other fallback. That
              is what gives the quality comparison something to measure against.
            </p>
          </Card>

          {/* 5 - Quality metrics, from the most recent golden-set run. */}
          <Card
            title="Quality metrics"
            hint="From the most recent golden-set run."
            right={
              metrics.quality ? (
                <Link
                  href="/quality"
                  className="text-[11px] text-accent underline hover:brightness-110"
                >
                  see the run
                </Link>
              ) : undefined
            }
          >
            {metrics.quality ? (
              <>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Stat
                    label="cases passed"
                    value={`${metrics.quality.passed}/${metrics.quality.total_cases}`}
                  />
                  <Stat
                    label="precision at top"
                    value={metrics.quality.precision_at_k?.toFixed(3) ?? "—"}
                  />
                  <Stat
                    label="contradiction rate"
                    value={metrics.quality.contradiction_rate?.toFixed(3) ?? "—"}
                  />
                  <Stat
                    label="provenance"
                    value={metrics.quality.provenance_completeness?.toFixed(3) ?? "—"}
                  />
                </div>
                <p className="mt-3 text-[11px] text-faint">
                  Provenance completeness is a release gate (abc.md:361). Run it
                  again with{" "}
                  <span className="font-mono">python scripts/run_golden_set.py</span>.
                </p>
              </>
            ) : (
              <div>
                <Badge tone="warn">no run yet</Badge>
                <p className="mt-2 text-sm text-muted">
                  Run the golden set to fill this in:
                </p>
                <pre className="mt-2 rounded-lg border border-edge bg-base p-3 font-mono text-[11px] text-muted">
                  python scripts/run_golden_set.py
                </pre>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
