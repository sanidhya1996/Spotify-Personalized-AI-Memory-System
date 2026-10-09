// Why this file exists
// ====================
//
// A demo of the step AFTER this system: turning the context pack into songs.
//
// abc.md stops at the pack - "consumed by an AI orchestrator" - and choosing
// songs is that orchestrator's job, outside this project. This route stands in
// for it so a demo can end in music instead of a JSON block. It is labelled a
// demo on screen and nothing in the backend depends on it.
//
// It searches Apple's free iTunes Search API (no key, 30-second previews) on
// the console's own server, so the browser never calls Apple directly.
//
//   GET /api/songs?term=arijit singh romantic&exclude=heavy,metal

import { NextRequest } from "next/server";

// One song, trimmed to what the screen shows.
type Song = {
  title: string;
  artist: string;
  album: string;
  genre: string;
  artwork: string;
  preview: string;
  link: string;
};

// Search iTunes and drop any song whose genre matches an excluded word.
export async function GET(request: NextRequest) {
  const term = request.nextUrl.searchParams.get("term")?.trim() ?? "";
  const exclude = (request.nextUrl.searchParams.get("exclude") ?? "")
    .toLowerCase()
    .split(",")
    .map((word) => word.trim())
    .filter(Boolean);

  if (!term) {
    return Response.json({ term, songs: [] });
  }

  const url =
    "https://itunes.apple.com/search?entity=song&limit=15&term=" +
    encodeURIComponent(term);

  let data: { results?: Record<string, string>[] };
  try {
    const response = await fetch(url, { cache: "no-store" });
    data = await response.json();
  } catch {
    return Response.json(
      { term, songs: [], error: "Could not reach the iTunes Search API. Is this computer online?" },
      { status: 503 },
    );
  }

  const songs: Song[] = (data.results ?? [])
    .filter((track) => track.previewUrl)
    .filter((track) => {
      const genre = (track.primaryGenreName ?? "").toLowerCase();
      return !exclude.some((word) => genre.includes(word));
    })
    .slice(0, 5)
    .map((track) => ({
      title: track.trackName,
      artist: track.artistName,
      album: track.collectionName,
      genre: track.primaryGenreName,
      artwork: track.artworkUrl100,
      preview: track.previewUrl,
      link: track.trackViewUrl,
    }));

  return Response.json({ term, songs });
}
