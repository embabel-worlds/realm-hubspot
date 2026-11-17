/**
 * HubSpot allows five search calls a second per portal. A traversal over an owner's contacts asks
 * one search per contact, so every search producer must share the paced `hubspot-search` bucket,
 * or a 40-contact question is refused with 429s and answered half-empty.
 */
/// <reference types="vite/client" />
import { describe, it, expect } from "vitest";
import producersYml from "../producers/hubspot.yml?raw";
import typesYml from "../types/hubspot.yml?raw";
import specText from "../apis/hubspot-crm.json?raw";

// shortcut: splits on top-level `- name:` entries, which is all producers/hubspot.yml uses; parse YAML if it nests.
const producers = producersYml.split(/^- name: /m).slice(1).map((block: string) => ({
  name: block.split("\n")[0].trim(),
  search: /^ {2}operation: objectsSearch$/m.test(block),
  cost: block.match(/^ {2}cost: \{ bucket: (\S+), rate: (\d+)\/(\d+)s \}$/m),
  block,
}));

describe("producers/hubspot.yml", () => {
  it("paces every search producer in one bucket below HubSpot's five a second", () => {
    const search = producers.filter((p) => p.search);
    expect(search.length).toBeGreaterThan(0);
    const unpaced = search.filter((p) => !p.cost || p.cost[1] !== "hubspot-search").map((p) => p.name);
    expect(unpaced).toEqual([]);
    for (const p of search) expect(Number(p.cost![2]) / Number(p.cost![3])).toBeLessThan(5);
  });

  // Owners are CRM users, not graph people: without a bare-match door, "Mike's contacts" finds no Mike.
  it("lets a bare MATCH (o:HubSpotOwner) sweep the portal's owners", () => {
    const owner = typesYml.split(/^- name: /m).find((b: string) => b.startsWith("HubSpotOwner\n")) ?? "";
    const door = owner.match(/anchorLabel: HubSpotBook, relationship: HAS_OWNER,[^}]*/)?.[0] ?? "";
    expect(door).toMatch(/producer: ownersAll, implicitDoor: true/);
    const all = producers.find((p) => p.name === "ownersAll")?.block ?? "";
    // The door joins on the key the producer echoes; a mismatch matches no owner.
    expect(all.match(/^ {2}echoKeyAs: (\S+)$/m)?.[1]).toBe(door.match(/recordKeyField: (\w+)/)?.[1]);
    // Records come from the array the owners response actually has.
    const field = all.match(/^ {2}records: "\$\.(\w+)\[\*\]"$/m)?.[1] ?? "";
    const owners = JSON.parse(specText).components.schemas.CollectionResponsePublicOwner;
    expect(owners.properties[field]?.type).toBe("array");
  });

  // ownersList returns 100 owners a page; an unpaged read misses every later colleague.
  it("walks every page of the owner list", () => {
    for (const name of ["ownersAll", "ownersByEmail"]) {
      const block = producers.find((p) => p.name === name)?.block ?? "";
      expect(block, name).toMatch(/^ {2}paging: \{ style: cursor,.*cursorParam: after,.*cursorPath: "\$\.paging\.next\.after" \}$/m);
    }
  });
});
