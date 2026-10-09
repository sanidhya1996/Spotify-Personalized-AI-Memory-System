"use client";

// Why this file exists
// ====================
//
// The context preview screen. abc.md:341 asks for exactly this: "Enter a
// current intent and surface; display candidate retrieval, ranking, policy
// removals, final context pack, and token usage."
//
// It is the screen that answers the question the whole system exists to make
// answerable - "why did the assistant say that?" - so it shows the pipeline in
// the order it runs, not a summary of the outcome.
//
// It calls two endpoints, on purpose:
//   POST /v1/memories/search    - the candidates and their score breakdown
//   POST /v1/context/compose    - what survived policy, and the pack itself
// Compose alone would show the result without the working.

import { useState } from "react";
import { ApiFailure, post } from "@/lib/api";
import { useSubject } from "@/lib/useSubject";
import type { ContextPackage, SearchResult, Surface } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  ErrorNote,
  Field,
  ScoreBar,
  Stat,
  inputClass,
  typeTone,
} from "@/components/ui";

// The weights the backend ranks with (backend `memory/retrieval.py`). Shown so
// an operator can see a signal's contribution, not just its raw value.
const WEIGHTS: Record<string, number> = {
  intent_fit: 0.3,
  explicitness: 0.25,
  confidence: 0.15,
  recency: 0.15,
  repetition: 0.1,
  negative_feedback: 0.05,
};

// Words that say nothing about which music, dropped before searching for songs.
const FILLER = new Set([
  "the", "listener", "loves", "love", "likes", "like", "enjoys", "prefers",
  "does", "not", "want", "wants", "no", "any", "some", "something", "songs",
  "song", "music", "play", "put", "on", "me", "a", "an", "by", "to", "of",
  "for", "and", "please", "when", "while", "i", "give", "played", "listen",
]);

// Keep the words that describe the music.
function keywords(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter((word) => word && !FILLER.has(word));
}

// A song from the demo search.
type Song = {
  title: string;
  artist: string;
  album: string;
  genre: string;
  artwork: string;
  preview: string;
  link: string;
};

export default function ContextPreviewPage() {
  // Whoever is logged in.
  const subjectId = useSubject();
  const [intent, setIntent] = useState("");
  const [surface, setSurface] = useState<Surface>("player");
  const [budget, setBudget] = useState(500);

  const [search, setSearch] = useState<SearchResult | null>(null);
  const [pack, setPack] = useState<ContextPackage | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);

  // What happened to the message in the background, shown under the box.
  const [captured, setCaptured] = useState("");

  // Capture the message as an interaction event (POST /v1/events), the way a
  // real Spotify surface records every interaction (abc.md §5.4). The worker
  // then decides what is worth remembering: "I love Arijit Singh" becomes a
  // preference, "play something now" usually becomes nothing. The gateway
  // adds the logged-in user's id, so it can only ever be their own memory.
  // A failure here never stops the answer.
  async function capture(message: string) {
    const key = `console_${Date.now()}`;
    try {
      await post("/v1/events", {
        schema_version: "1.0",
        event_type: "ai_interaction",
        surface,
        locale: "en-US",
        occurred_at: new Date().toISOString(),
        consent_state: "granted",
        source_event_id: key,
        idempotency_key: key,
        content: message,
      });
      setCaptured(
        "Also learning from this in the background - anything worth remembering appears in Memory explorer in about a minute.",
      );
    } catch {
      setCaptured("Answered, but this message could not be saved for learning.");
    }
  }

  // The demo song search that runs after the pack is built.
  const [songs, setSongs] = useState<Song[] | null>(null);
  const [songTerm, setSongTerm] = useState("");
  const [songError, setSongError] = useState("");

  // Stand in for the AI that would pick songs: search with the request plus
  // the top preference in the pack, and skip genres the pack excludes.
  async function findSongs(composed: ContextPackage) {
    const liked = composed.items.find((item) => item.memory_type !== "exclusion");
    const excluded = composed.items
      .filter((item) => item.memory_type === "exclusion")
      .flatMap((item) => keywords(item.fact));

    const words = [...keywords(intent), ...(liked ? keywords(liked.fact) : [])];
    const term = [...new Set(words)].join(" ");
    setSongTerm(term);

    try {
      const response = await fetch(
        `/api/songs?term=${encodeURIComponent(term)}&exclude=${encodeURIComponent(excluded.join(","))}`,
      );
      const body = await response.json();
      setSongs(body.songs ?? []);
      setSongError(body.error ?? "");
    } catch {
      setSongs([]);
      setSongError("The song search did not answer.");
    }
  }

  // Run the two calls the preview is built from, in the order the backend
  // runs them, and keep whichever results arrive.
  async function run() {
    setBusy(true);
    setFailure(null);
    setCaptured("");
    // Learn from it in the background; answer straight away.
    capture(intent);
    setSearch(null);
    setPack(null);
    setSongs(null);
    setSongError("");
    try {
      const found = await post<SearchResult>("/v1/memories/search", {
        subject_id: subjectId,
        intent,
        surface,
        limit: 20,
      });
      setSearch(found);

      const composed = await post<ContextPackage>("/v1/context/compose", {
        subject_id: subjectId,
        intent,
        surface,
        token_budget: budget,
      });
      setPack(composed);
      await findSongs(composed);
    } catch (error) {
      setFailure(error as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  // Which memory ids made it into the pack, so the candidate list can mark
  // each row as kept or dropped.
  const kept = new Set((pack?.items ?? []).map((item) => item.memory_id));

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">Context preview</h1>
        <p className="mt-1 text-sm text-muted">
          Talk to Spotify&apos;s AI. You see what it would be told about you, every
          step that decided it, and songs - and it learns from what you say.
        </p>
      </header>

      <Card
        title="The request"
        hint="Same fields an orchestrator sends to /v1/context/compose."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Subject" hint="You - the logged-in user. Nobody else's data is used.">
            <div className={`${inputClass} text-faint`}>{subjectId}</div>
          </Field>

          <Field label="Surface" hint="Policy allows different types per surface.">
            <select
              className={inputClass}
              value={surface}
              onChange={(event) => setSurface(event.target.value as Surface)}
            >
              <option value="player">player</option>
              <option value="chat">chat</option>
              <option value="search">search</option>
            </select>
          </Field>

          <div className="sm:col-span-2">
            <Field
              label="Talk to Spotify's AI"
              hint="Ask for music, or say what you like. You get an answer now, and it learns from what you say."
            >
              <input
                className={inputClass}
                placeholder="e.g. play something romantic - I love Arijit Singh, but no heavy metal"
                value={intent}
                onChange={(event) => setIntent(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && intent.trim() && !busy && run()}
              />
            </Field>
            {captured && <p className="mt-2 text-xs text-accent">{captured}</p>}
          </div>

          <Field label="Token budget" hint="50 to 4000. The pack is trimmed to fit.">
            <input
              className={inputClass}
              type="number"
              min={50}
              max={4000}
              value={budget}
              onChange={(event) => setBudget(Number(event.target.value))}
            />
          </Field>

          <div className="flex items-end">
            <Button onClick={run} disabled={busy || !intent.trim()}>
              {busy ? "Thinking..." : "Send"}
            </Button>
          </div>
        </div>
      </Card>

      {failure && (
        <ErrorNote
          code={failure.code}
          message={failure.message}
          correlationId={failure.correlationId}
        />
      )}

      {/* Step 1 and 2 - what was found, and how it ranked. */}
      {search && (
        <Card
          title="1 · Candidate retrieval and ranking"
          hint="Hybrid search over this subject's memories, scored by six weighted signals."
          right={
            <span className="font-mono text-[11px] text-faint">
              trace {search.trace_id}
            </span>
          }
        >
          <div className="mb-4 grid grid-cols-3 gap-2">
            <Stat label="candidates considered" value={search.considered} />
            <Stat label="ranked and returned" value={search.results.length} />
            <Stat label="kept in final pack" value={pack ? pack.items.length : "-"} />
          </div>

          {search.results.length === 0 ? (
            <p className="text-sm text-muted">
              Nothing matched this intent for this subject.
            </p>
          ) : (
            <ol className="flex flex-col gap-3">
              {search.results.map((memory, index) => {
                const inPack = kept.has(memory.memory_id);
                return (
                  <li
                    key={memory.memory_id}
                    className={`rounded-lg border p-3 ${
                      inPack ? "border-accent/30 bg-raised" : "border-edge bg-raised/40"
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-faint">#{index + 1}</span>
                      <Badge tone={typeTone(memory.memory_type)}>
                        {memory.memory_type}
                      </Badge>
                      <Badge tone={inPack ? "good" : "neutral"}>
                        {inPack ? "in pack" : pack ? "not in pack" : "pending"}
                      </Badge>
                      <span className="ml-auto font-mono text-xs text-muted">
                        score {memory.score.toFixed(3)}
                      </span>
                    </div>

                    <p className="mt-2 text-sm text-ink wrap-anywhere">{memory.fact}</p>

                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-faint">
                      <span className="font-mono">{memory.memory_id}</span>
                      <span>confidence {memory.confidence.toFixed(2)}</span>
                      <span>seen {memory.evidence_count}x</span>
                      {memory.entities.length > 0 && (
                        <span>about {memory.entities.join(", ")}</span>
                      )}
                    </div>

                    {/* The score, taken apart. A total with no breakdown cannot
                        be argued with when it ranks something wrongly. */}
                    <div className="mt-3 flex flex-col gap-1.5">
                      {Object.entries(memory.signals).map(([name, value]) => (
                        <ScoreBar
                          key={name}
                          label={`${name} ${WEIGHTS[name] ? `(${WEIGHTS[name]})` : ""}`}
                          value={value}
                        />
                      ))}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </Card>
      )}

      {/* Step 3 - what policy took away. */}
      {pack && (
        <Card
          title="2 · Policy removals"
          hint="Retention, surface eligibility, superseded corrections and the confidence floor."
        >
          {pack.removed.length === 0 ? (
            <p className="text-sm text-muted">
              Nothing was removed by policy for this request.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {pack.removed.map((line, index) => (
                <li
                  key={`${line}-${index}`}
                  className="rounded-lg border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-muted wrap-anywhere"
                >
                  {line}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {/* Step 4 and 5 - the pack, and what it costs. */}
      {pack && (
        <Card
          title="3 · Final context pack and token usage"
          hint="Exactly what an orchestrator receives."
          right={
            <span className="font-mono text-[11px] text-faint">
              trace {pack.trace_id}
            </span>
          }
        >
          <div className="mb-4 grid grid-cols-3 gap-2">
            <Stat label="items in pack" value={pack.items.length} />
            <Stat label={`tokens of ${budget}`} value={pack.token_estimate} />
            <Stat label="memory used" value={pack.no_memory ? "no" : "yes"} />
          </div>

          {/* Budget as a bar, because "412 of 500" is easier to read than a
              number on its own. */}
          <div className="mb-4">
            <div className="h-2 w-full rounded-full bg-raised">
              <div
                className={`h-2 rounded-full ${
                  pack.token_estimate > budget ? "bg-bad" : "bg-accent"
                }`}
                style={{
                  width: `${Math.min(100, (pack.token_estimate / budget) * 100)}%`,
                }}
              />
            </div>
            <p className="mt-1 text-[11px] text-faint">
              {Math.max(0, budget - pack.token_estimate)} tokens left for the rest
              of the prompt
            </p>
          </div>

          {pack.no_memory ? (
            <div className="rounded-lg border border-info/30 bg-info/5 px-3 py-2 text-sm">
              <div className="font-semibold text-info">No memory used</div>
              <p className="mt-1 text-muted">{pack.reason}</p>
              <p className="mt-1 text-[11px] text-faint">
                The assistant still answers - it just answers without memory.
              </p>
            </div>
          ) : (
            <>
              <ol className="flex flex-col gap-2">
                {pack.items.map((item) => (
                  <li
                    key={item.memory_id}
                    className="rounded-lg border border-edge bg-raised p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={typeTone(item.memory_type)}>{item.memory_type}</Badge>
                      <Badge tone={item.source_class === "stated" ? "good" : "warn"}>
                        {item.source_class}
                      </Badge>
                      <span className="ml-auto text-[11px] text-faint">
                        confidence {item.confidence.toFixed(2)} · seen{" "}
                        {item.evidence_count}x
                      </span>
                    </div>
                    <p className="mt-2 text-sm text-ink wrap-anywhere">{item.fact}</p>
                    <p className="mt-1 text-[11px] text-faint wrap-anywhere">
                      included because: {item.relevance_reason}
                    </p>
                    <p className="mt-1 font-mono text-[11px] text-faint">
                      {item.memory_id}
                    </p>
                  </li>
                ))}
              </ol>

              {/* The rendered block, fences and all. The fence markers carry a
                  random suffix per request so stored text cannot close them -
                  worth seeing, because it is the injection defence. */}
              <div className="mt-4">
                <div className="mb-1 flex items-center gap-2">
                  <h3 className="text-xs font-semibold text-muted">
                    The text sent to the model
                  </h3>
                  <Badge tone="info">fenced as data</Badge>
                </div>
                <pre className="max-h-80 overflow-auto rounded-lg border border-edge bg-base p-3 font-mono text-[11px] leading-relaxed text-muted">
                  {pack.context_block}
                </pre>
                <p className="mt-1 text-[11px] text-faint wrap-anywhere">
                  fence for this request:{" "}
                  <span className="font-mono">{pack.fence_open}</span>
                  {" ... "}
                  <span className="font-mono">{pack.fence_close}</span>
                </p>
              </div>
            </>
          )}
        </Card>
      )}

      {/* Step 6 - a demo of what the AI would do with the pack. */}
      {songs && (
        <Card
          title="4 · Songs (demo)"
          hint="Not part of the memory system: a stand-in for the AI that would pick songs from this pack. 30-second previews from the iTunes Search API."
        >
          <p className="mb-3 text-[11px] text-faint wrap-anywhere">
            searched for: <span className="font-mono">{songTerm || "-"}</span>
          </p>

          {songError && <p className="mb-3 text-sm text-bad">{songError}</p>}

          {songs.length === 0 && !songError ? (
            <p className="text-sm text-muted">No songs found for this search.</p>
          ) : (
            <ol className="flex flex-col gap-2">
              {songs.map((song) => (
                <li
                  key={song.preview}
                  className="flex flex-wrap items-center gap-3 rounded-lg border border-edge bg-raised p-3"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={song.artwork}
                    alt=""
                    width={48}
                    height={48}
                    className="rounded"
                  />
                  <div className="min-w-0 flex-1">
                    <a
                      href={song.link}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm font-semibold text-ink hover:underline wrap-anywhere"
                    >
                      {song.title}
                    </a>
                    <p className="text-[11px] text-faint wrap-anywhere">
                      {song.artist} · {song.album} · {song.genre}
                    </p>
                  </div>
                  <audio controls preload="none" src={song.preview} className="h-8" />
                </li>
              ))}
            </ol>
          )}
        </Card>
      )}
    </div>
  );
}
