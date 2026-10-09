"use client";

// Why this file exists
// ====================
//
// The sidebar. One entry per screen the requirement names, and nothing else.
//
// abc.md:339-345 lists seven screens for this console. All seven are here, in
// the order the document lists them, so the sidebar can be read against the
// requirement line by line.

import Link from "next/link";
import { usePathname } from "next/navigation";

// The seven screens of abc.md:339-345, in the document's own order.
const SCREENS = [
  { href: "/", label: "Overview", note: "health, ingestion lag, rejections" },
  { href: "/memories", label: "Memory explorer", note: "one subject's memories" },
  { href: "/context", label: "Context preview", note: "retrieval, ranking, the pack" },
  { href: "/corrections", label: "Correction & deletion", note: "correct, remove, propagation" },
  { href: "/policy", label: "Schema & policy", note: "allowed fields, retention" },
  { href: "/quality", label: "Quality review", note: "golden-set runs" },
  { href: "/trace", label: "Audit trace", note: "decisions behind a response" },
];

export default function Nav() {
  const path = usePathname();

  // The login page stands on its own, with no menu.
  if (path === "/login") return null;

  return (
    <nav className="flex w-60 shrink-0 flex-col gap-1 border-r border-edge bg-panel p-3">
      <div className="px-2 pb-3">
        <div className="text-sm font-semibold text-ink">Memory console</div>
        <div className="text-[11px] text-faint">operator views</div>
      </div>

      {SCREENS.map((screen) => (
        <Link
          key={screen.href}
          href={screen.href}
          className={`rounded-lg px-2 py-2 transition ${
            path === screen.href
              ? "bg-raised text-ink"
              : "text-muted hover:bg-raised/60 hover:text-ink"
          }`}
        >
          <div className="text-sm font-medium">{screen.label}</div>
          <div className="text-[11px] text-faint">{screen.note}</div>
        </Link>
      ))}
    </nav>
  );
}
