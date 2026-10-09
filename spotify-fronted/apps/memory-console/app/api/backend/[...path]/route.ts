// Why this file exists
// ====================
//
// The gateway: every page's call to the backend goes through here, as the
// person who is logged in - and only as them.
//
// Each person logs in with their own user id and password (app/login/page.tsx).
// Their pass sits in an httpOnly cookie, and lib/session.ts checks it on every
// request. No valid pass, no call - the page is sent to the login screen.
//
// Everyone sees only their own data:
//   - authentication: the pass is checked here and again by the backend
//   - authorization: the user id is taken from the pass, never from the
//     browser, and written into every request here - so a page cannot ask
//     for anybody else. The backend refuses a mismatch too (SUBJECT_MISMATCH).
//   - only the paths the pages need are allowed. GET /subjects, which lists
//     every user, is not one of them.
//
// The browser never holds a pass or the secret, and never calls port 8000,
// so CORS does not matter.

import { NextRequest } from "next/server";
import { BACKEND, readSession } from "@/lib/session";

// The paths the pages use. Everything else is refused.
//   v1/...         the ten endpoints - every one is scoped to the subject
//   metrics, quality/runs, policy, openapi.json, health
//                  counts, rules and schemas only - nobody's data
const ALLOWED = [
  /^v1\/.+$/,
  /^metrics$/,
  /^quality\/runs$/,
  /^policy$/,
  /^openapi\.json$/,
  /^health$/,
];

// The headers worth passing through in each direction.
const FORWARD_TO_BACKEND = ["content-type", "x-correlation-id"];
const RETURN_TO_BROWSER = ["content-type", "x-correlation-id"];

// A refusal in the same shape the backend uses.
function refuse(status: number, code: string, message: string): Response {
  return Response.json({ detail: { code, message, correlation_id: "" } }, { status });
}

// Check who is logged in, check the path, put their id in, forward, hand back.
async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const session = await readSession();
  if (!session) return refuse(401, "UNAUTHENTICATED", "Please log in.");

  const joined = path.join("/");
  if (!ALLOWED.some((pattern) => pattern.test(joined))) {
    return refuse(403, "NOT_ALLOWED_HERE", `This app may not call /${joined}.`);
  }

  // The logged-in user's id goes into the query string and the body,
  // overwriting anything the browser sent. Endpoints that take no subject
  // simply ignore it.
  const query = new URLSearchParams(request.nextUrl.search);
  if (query.has("subject_id") || joined.startsWith("v1/")) {
    query.set("subject_id", session.subjectId);
  }
  const search = query.toString();
  const target = `${BACKEND}/${joined}${search ? `?${search}` : ""}`;

  const headers = new Headers({ Authorization: `Bearer ${session.token}` });
  for (const name of FORWARD_TO_BACKEND) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  // A GET or DELETE has no body to read.
  const hasBody = request.method !== "GET" && request.method !== "DELETE";
  let body: string | undefined;
  if (hasBody) {
    const raw = await request.text();
    try {
      body = JSON.stringify({ ...(raw ? JSON.parse(raw) : {}), subject_id: session.subjectId });
    } catch {
      return refuse(422, "VALIDATION_FAILED", "the request body is not valid JSON");
    }
  }

  let response: Response;
  try {
    response = await fetch(target, { method: request.method, headers, body, cache: "no-store" });
  } catch {
    // The single most common thing to go wrong in a demo, so say which.
    return refuse(
      503,
      "BACKEND_UNREACHABLE",
      `The app cannot reach the backend at ${BACKEND}. Start it with: ` +
        "python -m uvicorn memory.api:app --port 8000",
    );
  }

  const out = new Headers();
  for (const name of RETURN_TO_BROWSER) {
    const value = response.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(await response.text(), { status: response.status, headers: out });
}

// Next needs one export per method. Each is the same two lines.
export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
