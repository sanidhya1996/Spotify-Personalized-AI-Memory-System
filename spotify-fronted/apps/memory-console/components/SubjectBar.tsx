"use client";

// Why this file exists
// ====================
//
// The bar across the top of every page: who is logged in, whether their
// memory is on, and the way out.
//
// There is no "view as somebody else" any more. Everyone logs in with their
// own user id and password and sees only their own data, so the bar only ever
// shows the logged-in user.
//
// It is also the guard: if nobody is logged in, or the pass has expired, it
// sends the browser to the login page. The gateway refuses every data call
// without a valid pass anyway (app/api/backend/[...path]/route.ts) - this just
// gets the person to the login screen instead of a page full of errors.
//
// Memory on / paused / off is the listener's consent (abc.md:136 - pause and
// opt-out), changed through PATCH /v1/consent. Pausing keeps memories but stops
// using them; switching off stops capture too. Neither deletes anything.

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Badge, Button, inputClass } from "./ui";

type Consent = "granted" | "paused" | "denied";

// What each consent state means, in words for the bar.
const LABELS: Record<Consent, string> = {
  granted: "memory on",
  paused: "memory paused",
  denied: "memory off",
};

export default function SubjectBar() {
  const path = usePathname();
  const [who, setWho] = useState("");
  const [consent, setConsent] = useState<Consent | null>(null);

  // Who is logged in; nobody means the login page.
  useEffect(() => {
    if (path === "/login") return;
    fetch("/api/auth/me").then(async (response) => {
      if (!response.ok) {
        window.location.href = "/login";
        return;
      }
      setWho((await response.json()).subject_id);
      const state = await fetch("/api/backend/v1/consent").then((r) => (r.ok ? r.json() : null));
      if (state) setConsent(state.state);
    });
  }, [path]);

  // Turn memory on, pause it, or switch it off, then reload so every page
  // reflects it.
  async function changeConsent(state: Consent) {
    await fetch("/api/backend/v1/consent", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state }),
    });
    window.location.reload();
  }

  // Forget the pass and go back to the login page.
  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  if (path === "/login" || !who) return null;

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-edge bg-panel px-4 py-2 text-sm">
      <span className="text-muted">
        Logged in as <span className="font-semibold text-ink">{who}</span>
      </span>

      {consent && (
        <>
          <Badge tone={consent === "granted" ? "good" : consent === "paused" ? "warn" : "bad"}>
            {LABELS[consent]}
          </Badge>
          <select
            className={`${inputClass} w-auto py-1`}
            value={consent}
            onChange={(event) => changeConsent(event.target.value as Consent)}
          >
            <option value="granted">Memory: on</option>
            <option value="paused">Memory: paused (kept, not used)</option>
            <option value="denied">Memory: off</option>
          </select>
        </>
      )}

      <span className="ml-auto text-[11px] text-faint">you only ever see your own data</span>
      <Button variant="ghost" onClick={logout}>
        Log out
      </Button>
    </div>
  );
}
