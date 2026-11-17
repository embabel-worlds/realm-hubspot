/**
 * The write surface and its effect tiers, checked on the vendored spec as the host sees it:
 * the operations the host's tool filter lets through (`operation-ids` + `write-operation-ids`,
 * matched as embabel-api-client's `ApiModel.filterByOperationIds` matches them), classified the
 * way the host's `GatewayEffects` classifies them, and every write body checked the way the
 * captured host checks one (`CapturedApiWriteBody`: an object takes only the keys it declares).
 * Each "bypass" test tries a route around a rule and expects to find it closed.
 */
/// <reference types="vite/client" />
import { describe, it, expect } from "vitest";
import specText from "../apis/hubspot-crm.json?raw";
import apisYml from "../apis/apis.yml?raw";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any;
interface Op { id: string; method: string; path: string; op: Json }

const spec: Json = JSON.parse(specText);

// shortcut: reads a top-level `key:` followed by `- item` lines, which is all apis.yml uses; parse YAML if it grows flow lists.
function yamlList(key: string): string[] {
  const block = apisYml.match(new RegExp(`^  ${key}:\\n((?:    - .*\\n)+)`, "m"));
  return block ? block[1].split("\n").filter(Boolean).map((l: string) => l.replace(/^\s*- /, "").trim()) : [];
}
const reads = yamlList("operation-ids");
const writes = yamlList("write-operation-ids");

const METHODS = ["get", "put", "post", "delete", "options", "head", "patch", "trace"];
const allOps: Op[] = Object.entries(spec.paths).flatMap(([path, item]: [string, Json]) =>
  METHODS.filter((m) => item[m]).map((m) => ({ id: item[m].operationId, method: m, path, op: item[m] })),
);

const canonical = (id: string) => id.toLowerCase().replace(/[-/]/g, "_");
/** ApiModel.filterByOperationIds: exact ids first, then a case- and separator-insensitive fallback. */
function exposedBy(ids: string[]): Op[] {
  const exact = new Set(ids.filter((id) => allOps.some((o) => o.id === id)));
  const fallback = new Set(ids.filter((id) => !exact.has(id)).map(canonical));
  return allOps.filter((o) => exact.has(o.id) || fallback.has(canonical(o.id)));
}
const exposed = exposedBy([...reads, ...writes]);

/** GatewayEffects.classify, rules 1–3 (with operation-ids declared, rule 4 is never reached). */
function effectOf(o: Op): { changes: boolean; sensitive?: string } {
  const declared = o.op["x-embabel-effect"];
  if (declared) return { changes: true, sensitive: declared.sensitive };
  if (writes.map(canonical).includes(canonical(o.id))) return { changes: true };
  return { changes: false };
}

function deref(node: Json): Json {
  const ref: string | undefined = node?.$ref;
  return ref ? deref(ref.replace(/^#\//, "").split("/").reduce((n: Json, k: string) => n[k], spec)) : node;
}
const bodySchema = (o: Op): Json => deref(o.op.requestBody?.content?.["application/json"]?.schema);

/** CapturedApiWriteBody.validate: a key the schema does not declare, or a missing required one, refuses. */
function accepts(schema: Json, value: Json): boolean {
  const s = deref(schema);
  if (s.type === "object") {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
    const declared = s.properties ?? {};
    if (!Object.keys(value).every((k) => k in declared)) return false;
    if (!(s.required ?? []).every((k: string) => k in value)) return false;
    return Object.entries(value).every(([k, v]) => accepts(declared[k], v));
  }
  if (s.type === "array") return Array.isArray(value) && value.every((v) => accepts(s.items, v));
  return typeof value === (s.type === "integer" ? "number" : s.type) && (!s.enum || s.enum.includes(value));
}

/** Every keyword the captured host's schema walk accepts; anything else (additionalProperties, oneOf, …) it refuses. */
const SCHEMA_KEYWORDS = new Set(["type", "properties", "required", "description", "items", "enum", "format", "$ref"]);
function keywordsOf(schema: Json, seen: Json[] = []): string[] {
  const s = deref(schema);
  if (seen.includes(s)) return [];
  const own = Object.keys(schema).concat(schema.$ref ? Object.keys(s) : []);
  const children = [...Object.values(s.properties ?? {}), ...(s.items ? [s.items] : [])];
  return own.concat(children.flatMap((c) => keywordsOf(c, [...seen, s])));
}

const op = (id: string) => exposed.find((o) => o.id === id)!;
const writeOps = exposed.filter((o) => effectOf(o).changes);
const GENERIC_OR_PARTY = /^\/crm\/v3\/objects\/(contacts|companies|0-1|0-2|\{[^}]+\})(\/\{[^}]+\})?$/;

describe("the operation list the host exposes", () => {
  it("names only operations the spec has, each once, and no read is also a write", () => {
    for (const id of [...reads, ...writes]) expect(allOps.filter((o) => o.id === id), id).toHaveLength(1);
    expect(reads.filter((id) => writes.includes(id))).toEqual([]);
    expect(exposed.map((o) => o.id).sort()).toEqual([...reads, ...writes].sort());
  });

  it("stays inside the captured host's limits", () => {
    expect(reads.length).toBeGreaterThan(0);
    expect(writes.length).toBeLessThanOrEqual(64);
    expect(reads.length + writes.length).toBeLessThanOrEqual(128);
    for (const o of writeOps) expect(["post", "put", "patch", "delete"], o.id).toContain(o.method);
  });

  it("bypass: no exposed operation, and none in the vendored spec, can DELETE", () => {
    expect(exposed.filter((o) => o.method === "delete").map((o) => o.id)).toEqual([]);
    expect(allOps.filter((o) => o.method === "delete").map((o) => o.id)).toEqual([]);
  });

  it("bypass: no operation creates or updates a contact, a company or any object type generically", () => {
    const generic = (ops: Op[]) =>
      ops.filter((o) => o.method !== "get" && GENERIC_OR_PARTY.test(o.path)).map((o) => `${o.method} ${o.path}`);
    expect(generic(exposed)).toEqual([]);
    expect(generic(allOps)).toEqual([]);
    // The only non-GET under a generic {objectType} path is search, and it reads.
    expect(allOps.filter((o) => o.method !== "get" && o.path.includes("{objectType}")).map((o) => o.id)).toEqual(["objectsSearch"]);
  });
});

describe("effect tiers", () => {
  it("every write is listed in write-operation-ids and declares x-embabel-effect; every read declares none", () => {
    expect(writeOps.map((o) => o.id).sort()).toEqual([...writes].sort());
    for (const o of writeOps) expect(o.op["x-embabel-effect"]?.writes, o.id).toBeTruthy();
    for (const o of exposed.filter((x) => reads.includes(x.id))) {
      expect(o.op["x-embabel-effect"], o.id).toBeUndefined();
      expect(effectOf(o).changes, o.id).toBe(false);
    }
  });

  it("objectsSearch is a POST the host counts as a read", () => {
    expect(op("objectsSearch").method).toBe("post");
    expect(effectOf(op("objectsSearch")).changes).toBe(false);
  });

  it("the stage-only operation dealMoveStage is sensitive, and no other write is", () => {
    expect(effectOf(op("dealMoveStage")).sensitive).toMatch(/\w/);
    expect(writeOps.filter((o) => effectOf(o).sensitive).map((o) => o.id)).toEqual(["dealMoveStage"]);
  });

  it("every write body is closed: only keywords the captured host checks, no open map", () => {
    for (const o of writeOps.filter((x) => x.op.requestBody)) {
      expect(Object.keys(o.op.requestBody), o.id).toEqual(["content"]);
      expect(keywordsOf(o.op.requestBody.content["application/json"].schema).filter((k) => !SCHEMA_KEYWORDS.has(k)), o.id).toEqual([]);
    }
  });
});

describe("deal stage changes", () => {
  const stageMove = { properties: { dealstage: "closedwon" } };

  it("dealMoveStage accepts a stage change", () => {
    expect(accepts(bodySchema(op("dealMoveStage")), stageMove)).toBe(true);
  });

  it("bypass: the ordinary dealUpdate refuses dealstage and pipeline, alone or beside an allowed property", () => {
    const update = bodySchema(op("dealUpdate"));
    expect(accepts(update, { properties: { amount: "1000" } })).toBe(true);
    expect(accepts(update, stageMove)).toBe(false);
    expect(accepts(update, { properties: { amount: "1000", dealstage: "closedwon" } })).toBe(false);
    expect(accepts(update, { properties: { pipeline: "default" } })).toBe(false);
    expect(Object.keys(deref(update).properties.properties.properties)).not.toContain("dealstage");
  });

  it("bypass: no write but dealMoveStage takes dealstage, so no other route moves a stage", () => {
    const takesStage = writeOps.filter((o) => o.op.requestBody && accepts(bodySchema(o), stageMove)).map((o) => o.id);
    expect(takesStage).toEqual(["dealMoveStage"]);
  });

  it("bypass: an association attaches only a note or task, never rewires a contact or company", () => {
    const from = op("associationsCreateDefault").op.parameters.map(deref).find((p: Json) => p.name === "fromObjectType");
    expect(from.schema.enum).toEqual(["notes", "tasks"]);
  });
});
