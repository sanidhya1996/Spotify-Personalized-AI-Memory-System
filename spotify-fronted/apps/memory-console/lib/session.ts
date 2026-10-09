// Why this file exists
// ====================
//
// Who is logged in to this app, read from the session cookie.
//
// When a listener logs in, the backend (POST /auth/login) checks their
// password and returns a pass - a short-lived token for that listener only.
// app/api/auth/[action]/route.ts keeps that pass in an httpOnly cookie, so
// page scripts can never read or steal it.
//
// Every request then uses this file to find out who is logged in:
//   - authentication: the pass's signature and expiry are checked here, and
//     again by the backend on every call.
//   - authorization: the logged-in listener's id is the only id the gateway
//     (app/api/backend/[...path]/route.ts) ever sends, and the backend refuses
//     any pass used for somebody else's data (SUBJECT_MISMATCH).
//
// Server-side only: it reads the signing secret.

import { cookies } from "next/headers";
import { jwtVerify } from "jose";

export const SESSION_COOKIE = "spotifymem.session";

// Where the backend is. Server-side only.
export const BACKEND = process.env.MEMORY_API_BASE_URL ?? "http://127.0.0.1:8000";

// The same secret the backend signs passes with, used here only to check them.
const SECRET = process.env.MEMORY_JWT_SECRET;

export type Session = { subjectId: string; token: string };

// The logged-in listener, or null if nobody is, or the pass is fake or expired.
export async function readSession(): Promise<Session | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token || !SECRET) return null;
  try {
    const { payload } = await jwtVerify(token, new TextEncoder().encode(SECRET));
    return typeof payload.sub === "string" ? { subjectId: payload.sub, token } : null;
  } catch {
    return null;
  }
}

// Keep a new pass in the cookie, for exactly as long as the pass lasts.
export async function saveSession(token: string, seconds: number): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: seconds,
  });
}

// Log out: forget the pass.
export async function clearSession(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}
