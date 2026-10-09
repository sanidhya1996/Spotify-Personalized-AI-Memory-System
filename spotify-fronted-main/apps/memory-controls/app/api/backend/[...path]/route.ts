// Why this file exists
// ====================
//
// The gateway for the listener-facing app: every call to the backend goes
// through here, as the listener who is logged in.
//
// The listener logs in with their own user id and password (app/login/page.tsx).
// Their pass sits in an httpOnly cookie, and lib/session.ts checks it here on
// every request. No valid pass, no call - the page is sent to the login screen.
//
// The subject is never taken from the browser. It comes from the checked pass,
// and is written into every request here, so the page cannot ask for anybody
// else. The backend checks again on its side (bind_subject: SUBJECT_MISMATCH).
// One listener can never reach another listener's memories - abc.md's
// strongest requirement - and it holds by construction, not by a check alone.

import { NextRequest } from "next/server";
import { BACKEND, readSession } from "@/lib/session";

// Only the endpoints this app is allowed to reach. A listener's app has no
// business calling /metrics or extraction, so the list is closed rather than
// open - abc.md:127 requires narrow, authorized access, not a general proxy.
const ALLOWED = [
  /^v1\/memories\/search$/,
  /^v1\/memories\/[^/]+$/,
  /^v1\/deletions\/[^/]+$/,
  /^v1\/feedback$/,
  // Pause and opt out. abc.md:136 - the listener's own consent paths.
  /^v1\/consent$/,
  /^health$/,
];

// A refusal in the same envelope the backend uses, so the app handles it the
// same way as any other failure.
function refuse(status: number, code: string, message: string): Response {
  return Response.json({ detail: { code, message, correlation_id: "" } }, { status });
}

// Check who is logged in, check the path is allowed, forward, hand back.
async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const session = await readSession();
  if (!session) {
    return refuse(401, "UNAUTHENTICATED", "Please log in.");
  }
  const SUBJECT_ID = session.subjectId;

  const joined = path.join("/");
  if (!ALLOWED.some((pattern) => pattern.test(joined))) {
    return refuse(403, "NOT_ALLOWED_HERE", `This app may not call /${joined}.`);
  }

  // The subject is added here, not sent by the browser. The backend wants it in
  // the body on POST and PATCH, and in the query string on GET and DELETE, so
  // both are filled in from the logged-in listener. The page never mentions a subject at
  // all, which is what makes it impossible for it to ask for the wrong one.
  const query = new URLSearchParams(request.nextUrl.search);
  query.set("subject_id", SUBJECT_ID);
  const target = `${BACKEND}/${joined}?${query.toString()}`;

  const headers = new Headers({ Authorization: `Bearer ${session.token}` });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  // A GET or DELETE has no body to read.
  const hasBody = request.method !== "GET" && request.method !== "DELETE";

  // Put the subject into the body too, overwriting anything the browser sent.
  let body: string | undefined;
  if (hasBody) {
    const raw = await request.text();
    try {
      body = JSON.stringify({ ...(raw ? JSON.parse(raw) : {}), subject_id: SUBJECT_ID });
    } catch {
      return refuse(422, "VALIDATION_FAILED", "the request body is not valid JSON");
    }
  }

  let response: Response;
  try {
    response = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
    });
  } catch {
    return refuse(
      503,
      "BACKEND_UNREACHABLE",
      `Cannot reach the memory service at ${BACKEND}. Start it with: ` +
        "python -m uvicorn memory.api:app --reload --port 8000",
    );
  }

  const out = new Headers();
  const returned = response.headers.get("content-type");
  if (returned) out.set("content-type", returned);
  const correlation = response.headers.get("x-correlation-id");
  if (correlation) out.set("x-correlation-id", correlation);

  return new Response(await response.text(), { status: response.status, headers: out });
}

// Next needs one export per method. This app needs four; extraction and event
// capture are not among them.
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
