"use client";

// Why this file exists
// ====================
//
// Screen 5 of 7. abc.md:343 - "Schema and policy view: Read-only view for most
// roles; version history, allowed fields, retention, sensitivity, and rollout
// state."
//
// Read-only is the whole design: there is nothing to press on this screen, and
// no endpoint it calls can change anything.
//
// Allowed fields and the contract version are read live from the backend's own
// OpenAPI document, so this screen can never drift from the running service the
// way a copied table would. Every field, type, constraint and allowed value
// below is what the API will actually accept right now.
//
// Retention, sensitivity and retrieval eligibility come from GET /policy, which
// serves the very file the policy engine enforces. Nothing is copied into this
// screen, so a retention rule shown here cannot differ from the one applied.

import { useEffect, useState } from "react";
import { ApiFailure, get } from "@/lib/api";
import type { PolicyRegistry } from "@/lib/types";
import { Badge, Card, ErrorNote, Stat, typeTone } from "@/components/ui";

// Just the parts of an OpenAPI document this screen reads.
type OpenApi = {
  info: { title: string; version: string };
  paths: Record<string, Record<string, unknown>>;
  components?: { schemas?: Record<string, JsonSchema> };
};

type JsonSchema = {
  title?: string;
  description?: string;
  required?: string[];
  properties?: Record<string, FieldSchema>;
};

type FieldSchema = {
  type?: string;
  title?: string;
  description?: string;
  enum?: string[];
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  default?: unknown;
  anyOf?: FieldSchema[];
  items?: FieldSchema;
};

// The contracts worth showing, in the order a request travels through them.
// Named explicitly rather than listing all 25 schemas, because the internal ones
// are not part of what a caller may send.
const CONTRACTS = [
  { name: "Event", why: "What POST /v1/events accepts — the versioned event contract" },
  { name: "CreateMemoryRequest", why: "What POST /v1/memories accepts" },
  { name: "SearchRequest", why: "What POST /v1/memories/search accepts" },
  { name: "ComposeRequest", why: "What POST /v1/context/compose accepts" },
  { name: "PatchMemoryRequest", why: "What PATCH /v1/memories/{id} accepts" },
  { name: "FeedbackRequest", why: "What POST /v1/feedback accepts" },
  { name: "PolicyClass", why: "The policy class attached to every memory" },
];

// Why each memory type is kept as long as it is. The registry gives the
// numbers; these say what they are for, which is what an operator reading a
// retention table actually needs.
const RETENTION_REASON: Record<string, string> = {
  exclusion:
    "Longest of all. Forgetting an exclusion means doing the exact thing the listener asked us not to.",
  correction:
    "As long as the fact it corrects, or the old belief could resurface.",
  explicit_preference:
    "Stated outright, so the most trustworthy kind of memory and the longest-lived of the ordinary types.",
  candidate_preference:
    "A guess, not confirmed. Short life, and chat only, so an unconfirmed guess never silently steers playback.",
  episode:
    "One thing that happened. Useful for continuing a conversation, not for describing someone, so it fades quickly.",
};

// One field's constraints in a single readable line.
function constraints(field: FieldSchema): string {
  const parts: string[] = [];
  // A nullable field arrives as anyOf [type, null]; read the real half.
  const real = field.anyOf?.find((option) => option.type && option.type !== "null") ?? field;

  if (real.type === "array" && real.items?.type) parts.push(`array of ${real.items.type}`);
  else if (real.type) parts.push(real.type);
  if (field.anyOf?.some((option) => option.type === "null")) parts.push("optional");
  if (real.minLength !== undefined) parts.push(`min length ${real.minLength}`);
  if (real.maxLength !== undefined) parts.push(`max length ${real.maxLength}`);
  if (real.minimum !== undefined) parts.push(`min ${real.minimum}`);
  if (real.maximum !== undefined) parts.push(`max ${real.maximum}`);
  if (field.default !== undefined && field.default !== null) {
    parts.push(`default ${JSON.stringify(field.default)}`);
  }
  return parts.join(" · ");
}

export default function SchemaAndPolicyPage() {
  const [spec, setSpec] = useState<OpenApi | null>(null);
  const [policy, setPolicy] = useState<PolicyRegistry | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);

  // Read the live contract and the live registry once on load. Both are
  // read-only, so there is nothing to refresh and nothing to write back.
  useEffect(() => {
    get<OpenApi>("/openapi.json")
      .then(setSpec)
      .catch((error) => setFailure(error as ApiFailure));
    get<PolicyRegistry>("/policy")
      .then(setPolicy)
      .catch((error) => setFailure(error as ApiFailure));
  }, []);

  const schemas = spec?.components?.schemas ?? {};

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">Schema and policy</h1>
        <p className="mt-1 text-sm text-muted">
          Read-only. Every field below is read live from the running service, so
          it cannot be out of date.
        </p>
      </header>

      {failure && (
        <ErrorNote
          code={failure.code}
          message={failure.message}
          correlationId={failure.correlationId}
        />
      )}

      {spec && (
        <>
          <Card title="Contract version" hint="From the service's own OpenAPI document.">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="API version" value={spec.info.version} />
              <Stat
                label="event schema_version"
                value={policy?.event_schema_version ?? "—"}
              />
              <Stat label="endpoints" value={Object.keys(spec.paths).length} />
              <Stat label="rollout state" value={policy?.rollout_state ?? "—"} />
            </div>
            <p className="mt-3 text-[11px] text-faint">
              An event declaring any other schema_version is refused with
              UNSUPPORTED_SCHEMA_VERSION before anything is stored.
            </p>
          </Card>

          {/* Retention, sensitivity and retrieval eligibility - abc.md:343,
              from the registry the policy engine enforces. */}
          {policy && (
            <Card
              title="Policy registry"
              hint="Retention, sensitivity and retrieval eligibility per memory type — the same file the policy engine reads."
            >
              <ul className="flex flex-col gap-2">
                {Object.entries(policy.memory_types).map(([type, rules]) => (
                  <li
                    key={type}
                    className="rounded-lg border border-edge bg-raised px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={typeTone(type)}>{type}</Badge>
                      <span className="text-[11px] text-faint">
                        sensitivity{" "}
                        <span className="text-muted">{rules.sensitivity}</span>
                      </span>
                      <span className="ml-auto text-xs font-semibold tabular-nums text-ink">
                        kept {rules.retention_days} days
                      </span>
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className="text-[11px] text-faint">usable on</span>
                      {policy.surfaces.map((surface) => {
                        const allowed = rules.retrieval_eligibility.includes(surface);
                        return (
                          <span
                            key={surface}
                            className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                              allowed
                                ? "border-accent/40 text-accent"
                                : "border-edge text-faint line-through"
                            }`}
                          >
                            {surface}
                          </span>
                        );
                      })}
                    </div>

                    {RETENTION_REASON[type] && (
                      <p className="mt-1.5 text-[11px] text-faint">
                        {RETENTION_REASON[type]}
                      </p>
                    )}

                    {/* abc.md §7.2 step 1 - definition, example and
                        counterexample, from data/memory_types.yaml. */}
                    {policy.definitions?.[type] && (
                      <dl className="mt-2 grid gap-1 text-[11px]">
                        <div>
                          <dt className="inline font-semibold text-muted">What it is: </dt>
                          <dd className="inline text-ink">{policy.definitions[type].definition}</dd>
                        </div>
                        <div>
                          <dt className="inline font-semibold text-muted">Example: </dt>
                          <dd className="inline text-ink">{policy.definitions[type].example}</dd>
                        </div>
                        <div>
                          <dt className="inline font-semibold text-muted">Not this: </dt>
                          <dd className="inline text-ink">{policy.definitions[type].counterexample}</dd>
                        </div>
                      </dl>
                    )}
                  </li>
                ))}
              </ul>

              <p className="mt-3 text-[11px] text-faint">
                The retention numbers are a local choice — `abc.md` requires
                retention to vary by memory type and requires these three fields
                per type, but never states how long anything should live.
              </p>
            </Card>
          )}

          {/* abc.md §5.4 - retention also by geography and age, from
              data/retention_rules.yaml. They can only shorten it. */}
          {policy?.retention_rules && (
            <Card
              title="Retention by geography and age"
              hint="Applied on top of the per-type retention above. The strictest rule wins; none can make retention longer."
            >
              <ul className="flex flex-col gap-2 text-sm">
                <li className="rounded-lg border border-edge bg-raised px-3 py-2">
                  <span className="font-semibold text-ink">Strict data-protection regions</span>
                  <span className="ml-2 text-xs text-muted">
                    at most {policy.retention_rules.geography.strict_regions.max_retention_days} days
                  </span>
                  <p className="mt-1 font-mono text-[11px] text-faint wrap-anywhere">
                    {policy.retention_rules.geography.strict_regions.countries.join(" ")}
                  </p>
                </li>
                <li className="rounded-lg border border-edge bg-raised px-3 py-2">
                  <span className="font-semibold text-ink">Listeners under 18</span>
                  <span className="ml-2 text-xs text-muted">
                    at most {policy.retention_rules.age.under_18.max_retention_days} days
                  </span>
                </li>
              </ul>
            </Card>
          )}

          {/* Allowed fields - abc.md:343. */}
          {CONTRACTS.filter((contract) => schemas[contract.name]).map((contract) => {
            const schema = schemas[contract.name];
            const required = new Set(schema.required ?? []);
            return (
              <Card key={contract.name} title={contract.name} hint={contract.why}>
                <ul className="flex flex-col gap-1">
                  {Object.entries(schema.properties ?? {}).map(([field, spec]) => {
                    const allowed =
                      spec.enum ??
                      spec.anyOf?.find((option) => option.enum)?.enum ??
                      undefined;
                    return (
                      <li
                        key={field}
                        className="rounded-lg border border-edge bg-raised px-3 py-2"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-xs text-ink">{field}</span>
                          {required.has(field) ? (
                            <Badge tone="bad">required</Badge>
                          ) : (
                            <Badge>optional</Badge>
                          )}
                          <span className="ml-auto text-[11px] text-faint">
                            {constraints(spec)}
                          </span>
                        </div>

                        {/* Allowed values, where the contract fixes them. This is
                            the part that stops a caller inventing a value. */}
                        {allowed && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {allowed.map((value) => (
                              <span
                                key={value}
                                className="rounded border border-accent/30 px-1.5 py-0.5 font-mono text-[10px] text-accent"
                              >
                                {value}
                              </span>
                            ))}
                          </div>
                        )}

                        {spec.description && (
                          <p className="mt-1 text-[11px] text-faint wrap-anywhere">
                            {spec.description}
                          </p>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </Card>
            );
          })}
        </>
      )}

      {/* Version history. One contract version is live and enforced; there is
          no earlier one to list, and saying that is more honest than an empty
          table implying history was lost. */}
      <Card title="Version history" hint="abc.md:343">
        <div className="rounded-lg border border-edge bg-raised px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="good">
              event contract {policy?.event_schema_version ?? "1.0"}
            </Badge>
            <Badge tone="good">live</Badge>
            <span className="text-[11px] text-faint">the only version</span>
          </div>
          <p className="mt-1.5 text-[11px] text-faint">
            This is the first contract version, so there is no earlier one to
            migrate from. An event declaring any other version is refused with
            UNSUPPORTED_SCHEMA_VERSION, which is what makes a future version a
            deliberate migration rather than a silent change.
          </p>
        </div>
      </Card>
    </div>
  );
}
