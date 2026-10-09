// Why this file exists
// ====================
//
// Sends anyone who is not logged in straight to the login page, on the
// server, before a page is sent.
//
// Before, every page loaded first and only then asked the browser-side check
// (components/SubjectBar.tsx) whether someone was logged in - so opening the
// site showed the app for a moment before jumping to /login. Next.js runs
// this file before a route renders (in this version the file is `proxy.ts`;
// it was called `middleware`), so a visitor without a valid pass never sees
// a page they cannot use.
//
// It checks the same pass the gateway checks (lib/session.ts): signature and
// expiry, with the shared MEMORY_JWT_SECRET. The browser-side check stays as
// a second line, for a pass that expires while a page is open.

import { NextRequest, NextResponse } from "next/server";
import { jwtVerify } from "jose";

const SESSION_COOKIE = "spotifymem.session";

// Is there a genuine, unexpired login pass on this request?
async function loggedIn(request: NextRequest): Promise<boolean> {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const secret = process.env.MEMORY_JWT_SECRET;
  if (!token || !secret) return false;
  try {
    await jwtVerify(token, new TextEncoder().encode(secret));
    return true;
  } catch {
    return false;
  }
}

export async function proxy(request: NextRequest) {
  if (await loggedIn(request)) return NextResponse.next();
  return NextResponse.redirect(new URL("/login", request.url));
}

// Only the seven screens. The login page, the API routes (which answer
// "Please log in" themselves) and static files are left alone.
export const config = {
  matcher: ["/", "/memories", "/context", "/corrections", "/policy", "/quality", "/trace"],
};
