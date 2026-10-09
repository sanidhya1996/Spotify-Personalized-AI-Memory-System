// Why this file exists
// ====================
//
// The frame for the listener-facing app. Deliberately plainer than the console:
// no sidebar, no subject picker, no operator language.
//
// abc.md:253 names this app "Review, correction, deletion UI", and the Product
// Design Lead in the transcript is specific about the tone: "Users should not see
// a technical graph. They need a clear experience: 'Spotify remembered this
// preference,' with the ability to correct or remove it."

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Your memory controls",
  description:
    "Review, correct, remove or pause what Spotify's AI remembers about you.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full antialiased">
        <header className="border-b border-edge bg-panel px-6 py-4">
          <h1 className="text-base font-semibold text-ink">Your memory controls</h1>
          <p className="mt-0.5 text-xs text-muted">
            What Spotify&apos;s AI remembers about you, and how to change it.
          </p>
        </header>
        <main className="px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
