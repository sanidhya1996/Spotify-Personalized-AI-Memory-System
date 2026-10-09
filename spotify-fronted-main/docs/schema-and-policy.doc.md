# Schema and policy — screen document

**Screen 5 of 7** · **Route:** `/policy` · **File:** `apps/memory-console/app/policy/page.tsx`

**Requirement — `abc.md:343`:** *"Schema and policy view: Read-only view for most
roles; version history, allowed fields, retention, sensitivity, and rollout
state."*

---

## Call flow

Two reads on load, both read-only.

1. **`get<OpenApi>("/openapi.json")`** — `lib/api.ts`
   → `/api/backend/openapi.json` on the console's own server, which checks the
   login pass and forwards it.
   → **Backend `GET /openapi.json`** — FastAPI generates this from the Pydantic
   models in `memory/models.py`. It touches no store.

2. **`get<PolicyRegistry>("/policy")`** — `lib/api.ts`
   → **Backend `GET /policy`** → **`policy.registry()`** in `memory/policy.py`,
   which reads `data/policy_registry.yaml` — the same file the policy engine
   enforces when it decides what a surface may use.

3. **`setSpec()` / `setPolicy()`** — renders the contract version, the policy
   registry, one card per contract, and the version history.

The screen is read-only in the strongest sense: there is nothing to press, and
neither endpoint it uses can change anything.

---

## Why it reads the live contract instead of a copied table

Every field, type, constraint and allowed value on this screen is generated from
the models the running service validates against. A hand-written table would drift
the first time a field changed; this cannot. If the backend adds an `event_type`,
it appears here without anyone editing the frontend.

---

## What is shown, against the requirement

| Asked for | Shown |
|---|---|
| Read-only for most roles | Yes — nothing on the screen writes |
| Allowed fields | Yes — per contract, with type, required or optional, length and range limits, defaults and enumerated values |
| Retention | Yes — days per memory type, with why each is kept that long |
| Sensitivity | Yes — per memory type |
| Rollout state | Yes — from `GET /policy` |
| Version history | Yes — one contract version, live, and why there is no earlier one |

Retrieval eligibility is shown alongside retention, since it is the third field
`abc.md:292` requires per type: each surface appears either in accent as allowed
or struck through as not.

**Nothing is copied into this screen.** The retention numbers come from the very
file the policy engine reads, so a rule displayed here cannot differ from the one
applied. A duplicated policy table that silently disagreed with the enforced one
would be worse than an empty card.

The retention *values* are a local choice, and the screen says so: `abc.md`
requires retention to vary by memory type and requires these three fields per
type, but never states how long anything should live.

**Version history** is one live contract version. That is not a gap — this is the
first version, so there is no earlier one to migrate from, and an event declaring
any other version is refused with `UNSUPPORTED_SCHEMA_VERSION`. Saying that is
more honest than an empty table implying history was lost.

---

## Functions

| Function | File | Why it exists |
|---|---|---|
| `SchemaAndPolicyPage()` | `app/policy/page.tsx` | The screen; holds the fetched OpenAPI document. |
| `constraints()` | same | One field's type and limits as a single readable line, unwrapping the `anyOf [type, null]` shape a nullable field arrives in. |
| `CONTRACTS` | same | The seven contracts worth showing, in the order a request travels through them — not all 25 schemas, because the internal ones are not part of what a caller may send. |
| `NO_DATA_SOURCE` | same | What the requirement asks for that no endpoint reports, with the reason for each. |
| `get()` | `lib/api.ts` | The one GET this screen makes. |

---

## Worth pointing at

**The enumerated values.** `event_type` shows all seven accepted values,
`memory_type` all five, `surface` all three, `consent_state` all three. These are
the closed lists that stop a caller inventing a value — the same lists that make
`422 VALIDATION_FAILED` happen before any application code runs.
