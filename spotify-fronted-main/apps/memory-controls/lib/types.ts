// Why this file exists
// ====================
//
// The shapes this app actually uses, mirroring the Pydantic models in the
// backend's `memory/models.py` - same field names, same optionality.
//
// Deliberately smaller than the console's copy. This app can reach five
// endpoints (see app/api/backend/[...path]/route.ts), so it has no business
// knowing the shape of operational metrics or golden-set runs. Keeping the
// list short is part of keeping the app narrow.

// The error body every failing endpoint returns (memory/errors.py).
// The code is stable - screens branch on the code, never on the message.
export type ApiError = {
  code: string;
  message: string;
  correlation_id: string;
};

// The five memory types the backend allows (memory/models.py MEMORY_TYPES).
export type MemoryType =
  | "episode"
  | "explicit_preference"
  | "candidate_preference"
  | "exclusion"
  | "correction";

// The three product surfaces a request can come from.
export type Surface = "chat" | "player" | "search";

// --- POST /v1/memories/search --------------------------------------------

// One memory, as the search endpoint returns it.
export type RankedMemory = {
  memory_id: string;
  memory_type: string;
  fact: string;
  confidence: number;
  score: number;
  signals: Record<string, number>;
  entities: string[];
  evidence_count: number;

  // When it was written, and the window it is true for (abc.md:117).
  recorded_at: string | null;
  valid_from: string | null;
  valid_to: string | null;

  // active | superseded | expired.
  status: string;
};

export type SearchResult = {
  results: RankedMemory[];
  removed: string[];
  considered: number;
  trace_id: string;
};

// --- PATCH /v1/memories/{memory_id} --------------------------------------

export type MemoryUpdated = {
  memory_id: string;
  graph_version: number;
  status: string;

  // Set when a correction replaced an older memory. The old one is closed
  // and kept as history, never overwritten.
  superseded: string | null;
};

// --- DELETE /v1/memories/{id}, GET /v1/deletions/{job_id} ----------------

export type DeletionAccepted = {
  job_id: string;
  memory_id: string;
  status: string;
};

// One field per store, so a partial removal is visible rather than hidden
// behind a single flag.
export type DeletionStatus = {
  job_id: string;
  memory_id: string;
  status: string;
  stores: Record<string, string>;
  requested_at: string;
  completed_at: string | null;
  error: string | null;
};

// --- POST /v1/feedback ---------------------------------------------------

export type FeedbackRecorded = {
  feedback_id: string;
  recorded: boolean;

  // Whether this feedback was allowed to change the memory's standing.
  // False for anything the model produced (abc.md:149).
  reinforced: boolean;
  reinforce_reason: string;
};

// --- GET and PATCH /v1/consent -------------------------------------------

// Memory on, paused, or off - and what that means, in one line.
// abc.md:136 asks for "clear state", which a bare enum is not.
export type ConsentState = {
  subject_id: string;
  state: "granted" | "paused" | "denied";
  meaning: string;
};
