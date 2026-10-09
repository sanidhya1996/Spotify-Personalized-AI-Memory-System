// Why this file exists
// ====================
//
// The four login actions the listener app needs, on the app's own server:
//
//   POST /api/auth/signup   create an account (backend POST /auth/signup)
//   POST /api/auth/login    log in            (backend POST /auth/login)
//   POST /api/auth/logout   forget the pass
//   GET  /api/auth/me       who is logged in, or 401
//
// The browser never sees the pass. The backend returns it here, it goes
// straight into an httpOnly cookie (lib/session.ts), and only the user id is
// sent back to the page.
//
// Used by app/login/page.tsx (signup, login) and app/page.tsx (me, logout).

import { NextRequest } from "next/server";
import { BACKEND, clearSession, readSession, saveSession } from "@/lib/session";

// A refusal in the same shape the backend uses.
function refuse(status: number, code: string, message: string): Response {
  return Response.json({ detail: { code, message, correlation_id: "" } }, { status });
}

// Pass signup or login to the backend; on success keep the pass in the cookie.
async function forward(path: "signup" | "login", request: NextRequest): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${BACKEND}/auth/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await request.text(),
      cache: "no-store",
    });
  } catch {
    return refuse(503, "BACKEND_UNREACHABLE", `Cannot reach the memory service at ${BACKEND}.`);
  }

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    return Response.json(body, { status: response.status });
  }

  await saveSession(body.token, body.expires_in_seconds);
  return Response.json({ subject_id: body.subject_id });
}

export async function POST(request: NextRequest, context: { params: Promise<{ action: string }> }) {
  const { action } = await context.params;
  if (action === "signup" || action === "login") return forward(action, request);
  if (action === "logout") {
    await clearSession();
    return Response.json({ logged_out: true });
  }
  return refuse(404, "NOT_FOUND", "unknown action");
}

export async function GET(_request: NextRequest, context: { params: Promise<{ action: string }> }) {
  const { action } = await context.params;
  if (action !== "me") return refuse(404, "NOT_FOUND", "unknown action");

  const session = await readSession();
  if (!session) return refuse(401, "UNAUTHENTICATED", "Please log in.");
  return Response.json({ subject_id: session.subjectId });
}
