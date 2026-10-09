"use client";

// Why this file exists
// ====================
//
// The id of whoever is logged in, for any page that wants to show it or put
// it in a request.
//
// It is only a mirror. The gateway (app/api/backend/[...path]/route.ts) writes
// the logged-in user's id into every request itself, from the checked pass, so
// a page that sent the wrong id - or none yet - still only ever gets its own
// user's data.

import { useEffect, useState } from "react";

// The logged-in user's id; empty until known.
export function useSubject(): string {
  const [subject, setSubject] = useState("");

  useEffect(() => {
    fetch("/api/auth/me")
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => body && setSubject(body.subject_id))
      .catch(() => undefined);
  }, []);

  return subject;
}
