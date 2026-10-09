"use client";

// Why this file exists
// ====================
//
// Screen 6 of 7. abc.md:344 - "Quality review: Golden-set runs, failure
// clusters, multilingual cases, contradiction cases, and side-by-side
// memory-enabled comparisons."
//
// All five are here, from GET /quality/runs.
//
// The scores matter because they gate a release. abc.md:361 blocks launch if
// "provenance falls below threshold, or personalized output materially
// underperforms the memory-disabled baseline", so the three measures at the top
// are a gate rather than a report - and each one says plainly whether it passes.
//
// The cases come from data/golden-sets/pilot_golden_set.json, run by
// scripts/run_golden_set.py. Every case carries the five expectations
// abc.md:295 requires, and its category is what lets failures be clustered.

import { useEffect, useState } from "react";
import { ApiFailure, get } from "@/lib/api";
import type { GoldenCase, QualityRuns } from "@/lib/types";
import { Badge, Card, ErrorNote, Stat } from "@/components/ui";

// The six categories abc.md:148 names, in plain words.
const CATEGORY_LABEL: Record<string, string> = {
  explicit_preference: "Explicit preference",
  temporal_change: "Temporal change",
  contradiction: "Contradiction",
  multilingual: "Multilingual",
  sparse_history: "Sparse history",
  malicious_stored_text: "Malicious stored text",
};

// The release gates of abc.md:361, and what each has to clear.
const GATES = [
  {
    key: "provenance_completeness" as const,
    label: "Provenance completeness",
    floor: 1.0,
    higherIsBetter: true,
    why: "abc.md:361 blocks release if provenance falls below threshold. Every context item must carry its source class and the reason it was chosen.",
  },
  {
    key: "precision_at_k" as const,
    label: "Precision at the top",
    floor: 0.9,
    higherIsBetter: true,
    why: "abc.md:82 - precision at the top of the retrieved set, not just recall. One wrong memory can be more damaging than three missing ones.",
  },
  {
    key: "contradiction_rate" as const,
    label: "Contradiction rate",
    floor: 0.0,
    higherIsBetter: false,
    why: "abc.md:153 - how often a contradiction case was handled wrongly. Lower is better; zero is the target.",
  },
];

export default function QualityReviewPage() {
  const [data, setData] = useState<QualityRuns | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);

  useEffect(() => {
    get<QualityRuns>("/quality/runs")
      .then(setData)
      .catch((error) => setFailure(error as ApiFailure));
  }, []);

  const runs = data?.runs ?? [];
  const latest = runs[0] ?? null;
  const cases = data?.cases ?? [];

  // Failure clusters - abc.md:344. Group by the category each case belongs to,
  // so a pattern shows up instead of a list of individual failures.
  const clusters = cases.reduce<Record<string, { total: number; failed: number; cases: GoldenCase[] }>>(
    (groups, one) => {
      const group = groups[one.category] ?? { total: 0, failed: 0, cases: [] };
      group.total += 1;
      if (!one.passed) {
        group.failed += 1;
        group.cases.push(one);
      }
      groups[one.category] = group;
      return groups;
    },
    {},
  );

  const multilingual = cases.filter((one) => one.category === "multilingual");
  const contradiction = cases.filter((one) => one.category === "contradiction");

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">Quality review</h1>
        <p className="mt-1 text-sm text-muted">
          Golden-set runs, failure clusters and the memory-disabled comparison.
        </p>
      </header>

      {failure && (
        <ErrorNote
          code={failure.code}
          message={failure.message}
          correlationId={failure.correlationId}
        />
      )}

      {data && !latest && (
        <Card title="No run yet">
          <p className="text-sm text-muted">
            The golden set exists but has not been run. Run it from the backend
            repository:
          </p>
          <pre className="mt-2 rounded-lg border border-edge bg-base p-3 font-mono text-[11px] text-muted">
            python scripts/run_golden_set.py
          </pre>
        </Card>
      )}

      {latest && (
        <>
          {/* The release gates - abc.md:361. */}
          <Card
            title="Latest run"
            hint={latest.run_id}
            right={
              latest.failed === 0 ? (
                <Badge tone="good">all cases passed</Badge>
              ) : (
                <Badge tone="bad">{latest.failed} failed</Badge>
              )
            }
          >
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="cases" value={latest.total_cases} />
              <Stat label="passed" value={latest.passed} />
              <Stat label="failed" value={latest.failed} />
              <Stat
                label="finished"
                value={
                  latest.finished_at
                    ? new Date(latest.finished_at).toLocaleTimeString()
                    : "running"
                }
              />
            </div>

            <ul className="mt-4 flex flex-col gap-2">
              {GATES.map((gate) => {
                const value = latest[gate.key];
                const clears =
                  value === null
                    ? null
                    : gate.higherIsBetter
                      ? value >= gate.floor
                      : value <= gate.floor;
                return (
                  <li
                    key={gate.key}
                    className="rounded-lg border border-edge bg-raised px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-ink">{gate.label}</span>
                      <span className="font-mono text-sm text-muted">
                        {value === null ? "—" : value.toFixed(3)}
                      </span>
                      {clears === null ? (
                        <Badge>not measured</Badge>
                      ) : clears ? (
                        <Badge tone="good">clears the gate</Badge>
                      ) : (
                        <Badge tone="bad">blocks release</Badge>
                      )}
                      <span className="ml-auto text-[11px] text-faint">
                        {gate.higherIsBetter ? "target ≥" : "target ≤"}{" "}
                        {gate.floor.toFixed(2)}
                      </span>
                    </div>
                    <p className="mt-1 text-[11px] text-faint">{gate.why}</p>
                  </li>
                );
              })}
            </ul>
          </Card>

          {/* Failure clusters - abc.md:344. */}
          <Card
            title="Failure clusters"
            hint="Cases grouped by the category they belong to, so a pattern shows rather than a list."
          >
            <ul className="flex flex-col gap-2">
              {Object.entries(clusters)
                .sort((a, b) => b[1].failed - a[1].failed)
                .map(([category, group]) => (
                  <li
                    key={category}
                    className="rounded-lg border border-edge bg-raised px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-ink">
                        {CATEGORY_LABEL[category] ?? category}
                      </span>
                      <Badge tone={group.failed === 0 ? "good" : "bad"}>
                        {group.total - group.failed}/{group.total} passed
                      </Badge>
                    </div>
                    {group.cases.map((one) => (
                      <div
                        key={one.case_id}
                        className="mt-1.5 rounded border border-bad/30 bg-bad/5 px-2 py-1.5"
                      >
                        <span className="font-mono text-[11px] text-bad">
                          {one.case_id}
                        </span>
                        {one.failure_reason && (
                          <p className="mt-0.5 text-[11px] text-muted wrap-anywhere">
                            {one.failure_reason}
                          </p>
                        )}
                      </div>
                    ))}
                  </li>
                ))}
            </ul>
          </Card>

          {/* Multilingual cases - abc.md:344, called out by name. */}
          <Card
            title="Multilingual cases"
            hint="An intent in one language retrieving a memory stored in another."
          >
            {multilingual.length === 0 ? (
              <p className="text-sm text-muted">No multilingual cases in this run.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {multilingual.map((one) => (
                  <li
                    key={one.case_id}
                    className="flex flex-wrap items-center gap-2 rounded-lg border border-edge bg-raised px-3 py-1.5"
                  >
                    <Badge tone={one.passed ? "good" : "bad"}>
                      {one.passed ? "pass" : "fail"}
                    </Badge>
                    <span className="font-mono text-[11px] text-muted">
                      {one.locale}
                    </span>
                    <span className="text-xs text-ink">{one.case_id}</span>
                    <span className="ml-auto text-[11px] text-faint">
                      {one.matched_top}/{one.expected_top} expected memories found
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* Contradiction cases - abc.md:344, called out by name. */}
          <Card
            title="Contradiction cases"
            hint="Where one memory contradicts another, and the stated one has to win."
          >
            {contradiction.length === 0 ? (
              <p className="text-sm text-muted">No contradiction cases in this run.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {contradiction.map((one) => (
                  <li
                    key={one.case_id}
                    className="flex flex-wrap items-center gap-2 rounded-lg border border-edge bg-raised px-3 py-1.5"
                  >
                    <Badge tone={one.passed ? "good" : "bad"}>
                      {one.passed ? "pass" : "fail"}
                    </Badge>
                    <span className="text-xs text-ink">{one.case_id}</span>
                    <span className="ml-auto text-[11px] text-faint">
                      {one.prohibited_leaked === 0
                        ? "nothing prohibited leaked"
                        : `${one.prohibited_leaked} prohibited memory leaked`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* Side-by-side memory-enabled comparison - abc.md:344. */}
          {data?.experiment && (
            <Card
              title="Memory-enabled versus memory-disabled"
              hint={data.experiment.experiment}
              right={
                data.experiment.has_baseline ? (
                  <Badge tone="good">baseline exists</Badge>
                ) : (
                  <Badge tone="warn">no baseline arm</Badge>
                )
              }
            >
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2">
                  <div className="text-lg font-semibold tabular-nums text-accent">
                    {data.experiment.memory_enabled}
                  </div>
                  <div className="text-[11px] text-muted">
                    subjects answered WITH memory
                  </div>
                </div>
                <div className="rounded-lg border border-edge bg-raised px-3 py-2">
                  <div className="text-lg font-semibold tabular-nums text-ink">
                    {data.experiment.memory_disabled}
                  </div>
                  <div className="text-[11px] text-muted">
                    subjects answered WITHOUT memory
                  </div>
                </div>
              </div>

              <p className="mt-3 text-[11px] text-faint">
                A subject in the memory-disabled arm gets an explicit no-memory
                package, taking the same path as any other fallback rather than a
                special case — which is what makes it a fair baseline. Log in as
                user_003 (in the memory-disabled arm) and open Context preview
                to see it.
              </p>
            </Card>
          )}

          {/* Every run, so a regression between runs is visible. */}
          {runs.length > 1 && (
            <Card title="Earlier runs">
              <ul className="flex flex-col gap-1">
                {runs.slice(1).map((run) => (
                  <li
                    key={run.run_id}
                    className="flex flex-wrap items-center gap-2 rounded-lg border border-edge bg-raised px-3 py-1.5 text-[11px]"
                  >
                    <span className="font-mono text-muted">{run.run_id}</span>
                    <Badge tone={run.failed === 0 ? "good" : "bad"}>
                      {run.passed}/{run.total_cases}
                    </Badge>
                    <span className="text-faint">
                      precision {run.precision_at_k?.toFixed(3) ?? "—"}
                    </span>
                    <span className="ml-auto text-faint">
                      {run.started_at
                        ? new Date(run.started_at).toLocaleString()
                        : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
