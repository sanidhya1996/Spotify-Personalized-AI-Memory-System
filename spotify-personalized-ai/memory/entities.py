"""Why this file exists
=====================

abc.md:113 - "Resolve artists, tracks, albums, playlists, shows, episodes,
topics, activities, and contextual concepts to canonical identifiers."

The model gives us the words a listener used: "the weeknd", "The Weeknd",
"weeknd". Those are three different strings and one artist. Stored as
written, the same preference looks like three unrelated memories and a
search for any of them finds nothing.

This file turns a written name into a stable id, using the catalog in
data/catalog.yaml and the alias table abc.md:295 asks for.

Anything not in the catalog stays unresolved rather than being guessed at.
A wrong id is worse than no id: it attaches a memory to the wrong artist.
"""

import re
import unicodedata
from functools import cache
from pathlib import Path

import yaml

from memory.models import ResolvedEntity

CATALOG_PATH = Path(__file__).parent.parent / "data" / "catalog.yaml"

# abc.md:295 - "define confidence thresholds for ambiguous topic or
# activity concepts". Below this, we keep the name but no id.
MATCH_THRESHOLD = 0.85


# Strip accents, punctuation and spacing so "Beyoncé!" and "beyonce" match.
def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


# Load the catalog once and flatten it into one alias -> entity lookup.
@cache
def alias_table() -> dict[str, tuple[str, str, str]]:
    catalog = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))

    table: dict[str, tuple[str, str, str]] = {}
    for entity_type, entries in catalog.items():
        for entry in entries:
            names = [entry["name"], *entry.get("aliases", [])]
            for name in names:
                table[normalise(name)] = (entry["id"], entry["name"], entity_type)
    return table


# How alike two normalised names are, from 0.0 to 1.0.
def similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


# Turn one written name into a catalog entity, or leave it unresolved.
def resolve(name: str) -> ResolvedEntity:
    key = normalise(name)
    table = alias_table()

    # Exact alias match - the common case, and always trusted.
    if key in table:
        entity_id, canonical, entity_type = table[key]
        return ResolvedEntity(
            name=str(name).strip(),
            entity_id=entity_id,
            canonical_name=canonical,
            entity_type=entity_type,
            match_confidence=1.0,
        )

    # Near match, for small typos. abc.md:295 wants a threshold for
    # ambiguous concepts, so anything weaker is left unresolved.
    best_key, best_score = "", 0.0
    for candidate in table:
        score = similarity(key, candidate)
        if score > best_score:
            best_key, best_score = candidate, score

    if best_score >= MATCH_THRESHOLD:
        entity_id, canonical, entity_type = table[best_key]
        return ResolvedEntity(
            name=str(name).strip(),
            entity_id=entity_id,
            canonical_name=canonical,
            entity_type=entity_type,
            match_confidence=round(best_score, 3),
        )

    # Not in the catalog. Keep the words, claim no id - a wrong id would
    # attach this memory to the wrong artist.
    return ResolvedEntity(name=str(name).strip())


# Resolve a whole list of names, dropping duplicates that resolve alike.
def resolve_all(names: list[str]) -> list[ResolvedEntity]:
    resolved: list[ResolvedEntity] = []
    seen: set[str] = set()

    for name in names:
        entity = resolve(name)
        # Two spellings of one artist collapse into one entry.
        key = entity.entity_id or normalise(entity.name)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(entity)

    return resolved
