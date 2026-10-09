"use client";

// Why this file exists
// ====================
//
// Where a listener logs in, or signs up the first time.
//
// Sign up takes a user id that nobody else has, and a password; the backend
// (POST /auth/signup) refuses an id that already exists, so one person cannot
// take over somebody else's. Signing up also switches memory on, which is the
// listener's consent. Country and age are optional and only shorten how long
// memories are kept.
//
// On success the pass goes into an httpOnly cookie on this app's server
// (app/api/auth/[action]/route.ts) and the listener lands on their own page.
//
// A pilot stand-in for Spotify's login - in a real deployment the listener is
// already logged in to Spotify and this page does not exist.

import { useEffect, useState } from "react";
import { Button, Card, ErrorNote, inputClass } from "@/components/ui";

type Mode = "login" | "signup";

// Wait a little.
function pause(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Ask (and so wake) the backend; true once it answers.
async function isAwake(): Promise<boolean> {
  try {
    const response = await fetch("/api/wake", { cache: "no-store" });
    return (await response.json()).ready === true;
  } catch {
    return false;
  }
}

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>("login");
  const [subjectId, setSubjectId] = useState("");
  const [password, setPassword] = useState("");
  const [region, setRegion] = useState("");
  const [ageBand, setAgeBand] = useState("adult");
  const [problem, setProblem] = useState<{ code: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);

  // Is the backend awake? Free hosting sleeps when idle and takes up to a
  // minute to wake, so the page starts waking it the moment it opens
  // (app/api/wake/route.ts) - the app then works from its own URL alone.
  const [serverReady, setServerReady] = useState(false);
  const [waitingNote, setWaitingNote] = useState("");

  useEffect(() => {
    let stopped = false;
    (async () => {
      for (let i = 0; i < 40 && !stopped; i++) {
        if (await isAwake()) {
          setServerReady(true);
          return;
        }
        await pause(4000);
      }
    })();
    return () => {
      stopped = true;
    };
  }, []);

  // Log in or sign up, then go to the listener's own page.
  async function submit() {
    setBusy(true);
    setProblem(null);
    const body: Record<string, string> = {
      subject_id: subjectId.trim().toLowerCase(),
      password,
    };
    if (mode === "signup") {
      if (region.trim()) body.region = region.trim();
      body.age_band = ageBand;
    }

    // If the backend is still waking, wait for it and try again rather
    // than show an error.
    let response: Response | null = null;
    for (let attempt = 0; attempt < 6; attempt++) {
      response = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).catch(() => null);
      if (response && ![502, 503, 504].includes(response.status)) break;
      setWaitingNote("Waking up the server - free hosting sleeps when idle. This takes up to a minute...");
      while (!(await isAwake())) await pause(4000);
      setServerReady(true);
    }
    setWaitingNote("");

    if (response?.ok) {
      window.location.href = "/";
      return;
    }

    const detail = response ? (await response.json().catch(() => ({}))).detail : null;
    setProblem({
      code: detail?.code ?? "REQUEST_FAILED",
      // A 422 from the backend is a list of field errors; say it plainly.
      message: Array.isArray(detail)
        ? "User id: 3-40 lower-case letters, digits or _. Password: at least 8 characters. Country: 2 letters."
        : detail?.message ?? "Something went wrong. Is the app's server running?",
    });
    setBusy(false);
  }

  return (
    <div className="mx-auto flex max-w-md flex-col gap-4">
      {/* Whether the backend is awake - see the useEffect above. */}
      <p className={`text-xs ${serverReady ? "text-accent" : "text-muted"}`}>
        {serverReady
          ? "Server ready."
          : "Waking up the server (free hosting sleeps when idle) - you can type meanwhile..."}
      </p>
      {waitingNote && <p className="text-sm text-muted">{waitingNote}</p>}
      <Card
        title={mode === "login" ? "Log in" : "Create your account"}
        hint={
          mode === "login"
            ? "See and control what Spotify's AI remembers about you."
            : "Pick a user id nobody else has. Memory is switched on when you sign up - you can pause or switch it off any time."
        }
      >
        <div className="flex flex-col gap-3">
          <input
            className={inputClass}
            placeholder="user id, e.g. rahul_01"
            autoComplete="username"
            value={subjectId}
            onChange={(event) => setSubjectId(event.target.value)}
          />
          <input
            className={inputClass}
            type="password"
            placeholder={mode === "signup" ? "password (at least 8 characters)" : "password"}
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && submit()}
          />

          {mode === "signup" && (
            <div className="flex gap-2">
              <input
                className={inputClass}
                placeholder="country, e.g. IN (optional)"
                maxLength={2}
                value={region}
                onChange={(event) => setRegion(event.target.value.toUpperCase())}
              />
              <select
                className={inputClass}
                value={ageBand}
                onChange={(event) => setAgeBand(event.target.value)}
              >
                <option value="adult">18 or older</option>
                <option value="under_18">under 18</option>
              </select>
            </div>
          )}

          <Button onClick={submit} disabled={busy || !subjectId.trim() || !password}>
            {busy ? "Please wait..." : mode === "login" ? "Log in" : "Sign up"}
          </Button>

          <button
            type="button"
            className="text-left text-xs text-muted underline"
            onClick={() => {
              setMode(mode === "login" ? "signup" : "login");
              setProblem(null);
            }}
          >
            {mode === "login" ? "New here? Create an account" : "Already have an account? Log in"}
          </button>
        </div>
      </Card>

      {problem && <ErrorNote code={problem.code} message={problem.message} correlationId="" />}
    </div>
  );
}
