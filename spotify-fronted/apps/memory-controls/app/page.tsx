"use client";

// Why this file exists
// ====================
//
// The whole listener-facing app, on one page.
//
// abc.md:253 - "memory-controls/ # Review, correction, deletion UI"
// abc.md:51  - "Memory Control Experience: User-facing controls to review,
//               correct, remove, pause, or opt out of eligible memory behavior."
// abc.md:136 - "Provide review, correction, deletion, pause, and opt-out paths
//               with clear state and propagation status."
//
// All five work here against the live service. Pause and opt-out change consent
// state through PATCH /v1/consent, and the service enforces that state before
// memory ever reaches retrieval - so pausing takes effect on the very next
// request rather than at some later sync.
//
// Pausing is not deleting, and the screen says so. abc.md:137 keeps them
// separate: pausing stops memory being used, deleting removes it. Blurring the
// two would be the easiest way to mislead someone about what just happened to
// their data.
//
// The language is deliberately plain. No memory ids, no confidence scores, no
// graph vocabulary - the transcript's Product Design Lead asks for "Spotify
// remembered this preference", not a node inspector.

import { useEffect, useState } from "react";
import { ApiFailure, del, get, patch, post } from "@/lib/api";
import type {
  ConsentState,
  DeletionAccepted,
  DeletionStatus,
  MemoryUpdated,
  RankedMemory,
  SearchResult,
} from "@/lib/types";
import { Badge, Button, Card, ErrorNote, inputClass } from "@/components/ui";

// How to describe each memory type to the person it is about. The stored names
// are for operators; these are for listeners.
const IN_PLAIN_WORDS: Record<string, string> = {
  explicit_preference: "You told us this",
  exclusion: "You asked us not to",
  correction: "You corrected this",
  candidate_preference: "We noticed this",
  episode: "Something you did",
};

// Did the listener say it, or did we infer it? Only the second kind needs the
// softer "we noticed" framing and an easy way to say no.
function weGuessed(memoryType: string): boolean {
  return memoryType === "candidate_preference" || memoryType === "episode";
}

export default function MemoryControlsPage() {
  const [memories, setMemories] = useState<RankedMemory[]>([]);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<ApiFailure | null>(null);

  // Which memory is being edited, and the wording being typed.
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  // What just happened, in one line, so an action never completes silently.
  const [note, setNote] = useState<string | null>(null);

  // Deletion is not instant - it has to reach four stores - so its progress is
  // tracked per memory and shown until every store is accounted for.
  const [removing, setRemoving] = useState<string | null>(null);
  const [removal, setRemoval] = useState<DeletionStatus | null>(null);

  const [busy, setBusy] = useState(false);

  // Whether memory is on, paused or off for this listener.
  const [consent, setConsent] = useState<ConsentState | null>(null);

  // Read the current consent state, so the buttons show where things stand
  // rather than assuming.
  async function loadConsent() {
    try {
      setConsent(await get<ConsentState>("/v1/consent"));
    } catch (error) {
      setFailure(error as ApiFailure);
    }
  }

  // Pause, resume or switch memory off. abc.md:136 - the pause and opt-out
  // paths. Nothing is deleted: abc.md:137 keeps pausing and deleting separate.
  async function setConsentState(state: "granted" | "paused" | "denied") {
    setBusy(true);
    setFailure(null);
    try {
      const next = await patch<ConsentState>("/v1/consent", { state });
      setConsent(next);
      setNote(next.meaning);
      // A paused or switched-off listener should see the list reflect that
      // straight away rather than on the next visit.
      await load();
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  // Everything we hold about this listener. The subject is fixed on the server,
  // so there is nothing to pass here and nothing they could change.
  async function load() {
    setLoading(true);
    setFailure(null);
    try {
      const found = await post<SearchResult>("/v1/memories/search", {
        // A broad intent, because this is a review screen and not a search.
        intent: "everything you have told us",
        // chat allows the most memory types, so nothing is hidden from the
        // person it belongs to.
        surface: "chat",
        limit: 50,
      });
      setMemories(found.results);
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setLoading(false);
    }
  }

  // Who is logged in. Nobody, or an expired pass, means the login page.
  const [who, setWho] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/me").then(async (response) => {
      if (!response.ok) {
        window.location.href = "/login";
        return;
      }
      setWho((await response.json()).subject_id);
      load();
      loadConsent();
    });
  }, []);

  // Forget the pass and go back to the login page.
  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  // Correct the wording. The old version is kept as history rather than
  // overwritten, so a correction can itself be reviewed later.
  async function saveCorrection(memory: RankedMemory) {
    setBusy(true);
    setFailure(null);
    try {
      const updated = await patch<MemoryUpdated>(`/v1/memories/${memory.memory_id}`, {
        operation: "correct",
        expected_version: 1,
        fact: draft,
        entities: [],
        confidence: 1.0,
      });
      setEditing(null);
      setNote("Saved. We will use your wording from now on.");
      // Tell the service this came from the listener, not from us.
      await post("/v1/feedback", {
        kind: "correction",
        sentiment: "wrong",
        memory_id: updated.memory_id,
      }).catch(() => undefined);
      await load();
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  // Say this is wrong without rewriting it. abc.md:149 - negative feedback from
  // the listener always counts, even on something we only guessed.
  async function sayItsWrong(memory: RankedMemory) {
    setBusy(true);
    setFailure(null);
    try {
      await post("/v1/feedback", {
        kind: "rejection",
        sentiment: "wrong",
        memory_id: memory.memory_id,
      });
      setNote("Thanks. We will stop leaning on that.");
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  // Remove it everywhere, and keep watching until every store has answered.
  async function remove(memory: RankedMemory) {
    setBusy(true);
    setFailure(null);
    setRemoving(memory.memory_id);
    setRemoval(null);
    try {
      const accepted = await del<DeletionAccepted>(`/v1/memories/${memory.memory_id}`);
      for (let attempt = 0; attempt < 15; attempt += 1) {
        const status = await get<DeletionStatus>(`/v1/deletions/${accepted.job_id}`);
        setRemoval(status);
        if (status.status !== "pending" && status.status !== "in_progress") break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      await load();
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  // Which stores, if any, did not finish. Shown to the listener in plain words,
  // because abc.md:342 forbids letting a partial removal look complete.
  const stillThere = Object.entries(removal?.stores ?? {}).filter(
    ([, state]) => !["deleted", "nothing_to_delete", "retained_by_policy"].includes(state),
  );

  // Nothing to show until we know who is logged in.
  if (!who) return null;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted">
          Logged in as <span className="font-semibold text-ink">{who}</span>
        </span>
        <Button variant="ghost" onClick={logout}>
          Log out
        </Button>
      </div>

      {note && (
        <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-sm text-accent">
          {note}
        </div>
      )}

      {failure && (
        <ErrorNote
          code={failure.code}
          message={failure.message}
          correlationId={failure.correlationId}
        />
      )}

      {/* Review - the first of the five paths. */}
      <Card
        title="What we remember"
        hint="Only things you have told us, or that we noticed and marked as a guess."
        right={
          <Button variant="ghost" onClick={load} disabled={loading || busy}>
            Refresh
          </Button>
        }
      >
        {loading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : memories.length === 0 ? (
          <p className="text-sm text-muted">
            Nothing yet. As you use Spotify&apos;s AI features, anything worth
            remembering will appear here — and you can change or remove it.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {memories.map((memory) => (
              <li
                key={memory.memory_id}
                className="rounded-lg border border-edge bg-raised p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={weGuessed(memory.memory_type) ? "warn" : "good"}>
                    {IN_PLAIN_WORDS[memory.memory_type] ?? memory.memory_type}
                  </Badge>
                  {weGuessed(memory.memory_type) && (
                    <span className="text-[11px] text-faint">
                      a guess, not something you said
                    </span>
                  )}
                </div>

                {editing === memory.memory_id ? (
                  <div className="mt-2 flex flex-col gap-2">
                    <input
                      className={inputClass}
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      autoFocus
                    />
                    <div className="flex gap-2">
                      <Button
                        onClick={() => saveCorrection(memory)}
                        disabled={busy || !draft.trim() || draft === memory.fact}
                      >
                        {busy ? "Saving…" : "Save"}
                      </Button>
                      <Button variant="ghost" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="mt-2 text-sm text-ink wrap-anywhere">{memory.fact}</p>

                    {/* Correct and remove - paths two and three. */}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button
                        variant="ghost"
                        onClick={() => {
                          setEditing(memory.memory_id);
                          setDraft(memory.fact);
                          setNote(null);
                        }}
                      >
                        Change the wording
                      </Button>
                      {weGuessed(memory.memory_type) && (
                        <Button
                          variant="ghost"
                          onClick={() => sayItsWrong(memory)}
                          disabled={busy}
                        >
                          That&apos;s not right
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        onClick={() => remove(memory)}
                        disabled={busy}
                      >
                        {removing === memory.memory_id && busy ? "Removing…" : "Remove"}
                      </Button>
                    </div>
                  </>
                )}

                {/* Propagation status - abc.md:136 asks for it by name, and
                    abc.md:342 forbids a partial removal looking finished. */}
                {removing === memory.memory_id && removal && (
                  <div className="mt-3 rounded-lg border border-edge bg-base px-3 py-2">
                    {stillThere.length > 0 ? (
                      <>
                        <p className="text-sm font-semibold text-bad">
                          Not fully removed yet
                        </p>
                        <p className="mt-1 text-xs text-muted">
                          We could not finish removing this everywhere. It may
                          still be used. Please try again in a moment.
                        </p>
                      </>
                    ) : (
                      <>
                        <p className="text-sm font-semibold text-accent">Removed</p>
                        <p className="mt-1 text-xs text-muted">
                          Gone from everywhere we use it. A copy may remain in a
                          backup until that backup expires, which we cannot
                          delete early — but nothing will read it.
                        </p>
                      </>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Pause and opt out - paths four and five. */}
      <Card
        title="Pause or turn off memory"
        hint="Takes effect on your very next request. Nothing is deleted."
      >
        <div className="flex flex-col gap-3">
          {consent && (
            <div className="flex flex-wrap items-center gap-2">
              <Badge
                tone={
                  consent.state === "granted"
                    ? "good"
                    : consent.state === "paused"
                      ? "warn"
                      : "bad"
                }
              >
                {consent.state === "granted"
                  ? "memory is on"
                  : consent.state === "paused"
                    ? "memory is paused"
                    : "memory is off"}
              </Badge>
              <span className="text-xs text-muted">{consent.meaning}</span>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            {consent?.state !== "granted" && (
              <Button onClick={() => setConsentState("granted")} disabled={busy}>
                Turn memory back on
              </Button>
            )}
            {consent?.state !== "paused" && (
              <Button
                variant="ghost"
                onClick={() => setConsentState("paused")}
                disabled={busy}
              >
                Pause memory
              </Button>
            )}
            {consent?.state !== "denied" && (
              <Button
                variant="ghost"
                onClick={() => setConsentState("denied")}
                disabled={busy}
              >
                Turn memory off
              </Button>
            )}
          </div>

          {/* The distinction that matters most on this screen. */}
          <div className="rounded-lg border border-edge bg-base px-3 py-2">
            <p className="text-xs text-muted">
              <span className="font-semibold text-ink">Pausing is not deleting.</span>{" "}
              While paused, nothing you have told us is used — the assistant
              carries on without it — but nothing is removed either, so turning
              memory back on restores everything above.
            </p>
            <p className="mt-1.5 text-xs text-muted">
              To remove something for good, use <strong>Remove</strong> on it. That
              clears it from every store and tells you when each one is done.
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}
