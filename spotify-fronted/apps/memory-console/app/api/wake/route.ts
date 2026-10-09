// Why this file exists
// ====================
//
// Wakes the backend, so the app works from its own URL alone.
//
// The backend runs on a free host that sleeps after a while idle and takes up
// to a minute to wake. A login sent while it sleeps used to time out. The
// login page now calls this as soon as it opens - while the person is still
// typing - and keeps calling until the backend answers.
//
// It only asks GET /health, which is public and returns nothing about anyone,
// so it needs no login.
//
//   GET /api/wake  ->  {"ready": true}  or  {"ready": false}

import { BACKEND } from "@/lib/session";

// Ask the backend if it is up; a sleeping one starts waking on this request.
export async function GET() {
  try {
    const response = await fetch(`${BACKEND}/health`, {
      cache: "no-store",
      // Shorter than the host's own limit on this function, so we always
      // answer; the page simply asks again.
      signal: AbortSignal.timeout(8000),
    });
    return Response.json({ ready: response.ok });
  } catch {
    return Response.json({ ready: false });
  }
}
