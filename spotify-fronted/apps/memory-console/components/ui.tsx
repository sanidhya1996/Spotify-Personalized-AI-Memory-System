// Why this file exists
// ====================
//
// The small pieces every screen repeats: a panel, a labelled input, a button,
// a status pill, a score bar, an error note. Keeping them here means a screen
// file is mostly about its endpoint, not about padding and borders.
//
// Deliberately plain - no component library, no abstraction to learn.

import type { ReactNode } from "react";

// A titled panel. Every block of a screen sits in one of these.
export function Card({
  title,
  hint,
  right,
  children,
}: {
  title?: string;
  hint?: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-edge bg-panel">
      {title && (
        <header className="flex items-start justify-between gap-4 border-b border-edge px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {hint && <p className="mt-0.5 text-xs text-faint">{hint}</p>}
          </div>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

// A label above a control, used for every form field.
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-faint">{hint}</span>}
    </label>
  );
}

// Shared look for text inputs, selects and textareas.
export const inputClass =
  "w-full rounded-lg border border-edge bg-raised px-3 py-2 text-sm text-ink " +
  "placeholder:text-faint focus:border-accent focus:outline-none";

// The primary action on a screen.
export function Button({
  children,
  onClick,
  disabled,
  variant = "primary",
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "ghost";
  type?: "button" | "submit";
}) {
  const look =
    variant === "primary"
      ? "bg-accent text-black hover:brightness-110"
      : "border border-edge bg-raised text-ink hover:border-faint";
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${look}`}
    >
      {children}
    </button>
  );
}

// A small pill. Memory types get their own colour so the difference between a
// stated preference and an inferred one is visible at a glance.
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad" | "info";
}) {
  const tones = {
    neutral: "border-edge text-muted",
    good: "border-accent/40 text-accent",
    warn: "border-warn/40 text-warn",
    bad: "border-bad/40 text-bad",
    info: "border-info/40 text-info",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

// Which colour a memory type gets. Stated types are green because the
// listener said them; inferred types are amber because we guessed.
export function typeTone(memoryType: string): "good" | "warn" | "bad" | "neutral" {
  if (memoryType === "explicit_preference" || memoryType === "correction") return "good";
  if (memoryType === "exclusion") return "bad";
  if (memoryType === "candidate_preference" || memoryType === "episode") return "warn";
  return "neutral";
}

// A labelled 0-1 bar, used for every score and signal.
export function ScoreBar({
  label,
  value,
  width = 1,
}: {
  label: string;
  value: number;
  width?: number;
}) {
  const pct = Math.max(0, Math.min(1, value / (width || 1))) * 100;
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 shrink-0 text-[11px] text-faint">{label}</span>
      <div className="h-1.5 flex-1 rounded-full bg-raised">
        <div
          className="h-1.5 rounded-full bg-accent"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-10 shrink-0 text-right font-mono text-[11px] text-muted">
        {value.toFixed(2)}
      </span>
    </div>
  );
}

// What went wrong, with the backend's stable code and correlation id, because
// those are what makes a failure findable in the audit log.
export function ErrorNote({
  code,
  message,
  correlationId,
}: {
  code: string;
  message: string;
  correlationId?: string;
}) {
  return (
    <div className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm">
      <div className="font-mono text-xs font-semibold text-bad">{code}</div>
      <p className="mt-1 text-muted wrap-anywhere">{message}</p>
      {correlationId && (
        <p className="mt-1 font-mono text-[11px] text-faint">
          correlation_id {correlationId}
        </p>
      )}
    </div>
  );
}

// One number with a caption, for the counters across the top of a result.
export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-edge bg-raised px-3 py-2">
      <div className="text-lg font-semibold tabular-nums text-ink">{value}</div>
      <div className="text-[11px] text-faint">{label}</div>
    </div>
  );
}
