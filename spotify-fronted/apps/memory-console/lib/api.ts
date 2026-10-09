// Why this file exists
// ====================
//
// One place that talks to the backend. Every screen calls `post`, `get`,
// `patch` or `del` from here rather than using `fetch` itself, so the base
// path and the error shape are handled once instead of ten times.
//
// There is no token in this file, and that is the point. Calls go to
// /api/backend/... on the console's own server, which mints the token and
// forwards them (see app/api/backend/[...path]/route.ts). The browser never
// holds a token or a secret, and nobody has to paste one.
//
// Which subject we are acting as is decided by the server from a cookie, not
// sent from here - so a screen cannot ask for a subject it is not allowed to
// see just by changing a request body.

import type { ApiError } from "./types";

// Every call goes through the console's own server, never straight to the
// backend. That is what removes both the token handling and the need for CORS.
export const API_BASE = "/api/backend";

// An error carrying the backend's stable code, so a screen can say something
// useful instead of "request failed".
export class ApiFailure extends Error {
  code: string;
  status: number;
  correlationId: string;

  constructor(status: number, body: ApiError) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.correlationId = body.correlation_id;
  }
}

// Turn a failed response into an ApiFailure, whatever shape it arrived in.
async function toFailure(response: Response): Promise<ApiFailure> {
  let body: ApiError = {
    code: "REQUEST_FAILED",
    message: `${response.status} ${response.statusText}`,
    correlation_id: response.headers.get("X-Correlation-Id") ?? "",
  };
  try {
    const parsed = await response.json();
    // The backend wraps its envelope in `detail` (memory/errors.py), and the
    // proxy keeps that shape for its own errors too.
    const detail = parsed?.detail ?? parsed;
    if (detail && typeof detail === "object" && "code" in detail) {
      body = detail as ApiError;
    }
  } catch {
    // Not JSON - keep the status-line fallback above.
  }
  return new ApiFailure(response.status, body);
}

// The one function that actually makes a request. Everything below is a
// two-line wrapper over it.
async function request<T>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // fetch only throws when the request never left the browser, which now
    // means the console's own server is down, not the backend.
    throw new ApiFailure(0, {
      code: "CONSOLE_UNREACHABLE",
      message: "The console's own server did not answer. Is `npm run dev` still running?",
      correlation_id: "",
    });
  }

  // Not logged in, or the pass expired: back to the login page.
  if (response.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
  }
  if (!response.ok) throw await toFailure(response);
  return (await response.json()) as T;
}

// POST a JSON body - endpoints 1, 2, 3, 4, 5 and 9.
export function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>("POST", path, body);
}

// GET - endpoints 8 and 10, and /metrics.
export function get<T>(path: string): Promise<T> {
  return request<T>("GET", path);
}

// PATCH - endpoint 6, correcting or expiring a memory.
export function patch<T>(path: string, body: unknown): Promise<T> {
  return request<T>("PATCH", path, body);
}

// DELETE - endpoint 7, starting a cross-store deletion. Named `del` because
// `delete` is a reserved word.
export function del<T>(path: string): Promise<T> {
  return request<T>("DELETE", path);
}

// Is the backend up? Goes through the proxy like everything else, so a failure
// here means the same thing it means everywhere else.
export async function getHealth(): Promise<unknown> {
  return get<unknown>("/health");
}
