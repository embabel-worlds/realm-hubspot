/**
 * HubSpot allows five search calls a second per portal. A traversal over an owner's contacts asks
 * one search per contact, so every search producer must share the paced `hubspot-search` bucket,
 * or a 40-contact question is refused with 429s and answered half-empty.
 */
/// <reference types="vite/client" />
import { describe, it, expect } from "vitest";
import producersYml from "../producers/hubspot.yml?raw";
import typesYml from "../types/hubspot.yml?raw";

// shortcut: splits on top-level `- name:` entries, which is all producers/hubspot.yml uses; parse YAML if it nests.
const producers = producersYml.split(/^- name: /m).slice(1).map((block: string) => ({
  name: block.split("\n")[0].trim(),
  search: /^ {2}operation: objectsSearch$/m.test(block),
  cost: block.match(/^ {2}cost: \{ bucket: (\S+), rate: (\d+)\/(\d+)s \}$/m),
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
    expect(owner).toMatch(/anchorLabel: HubSpotBook, relationship: HAS_OWNER,[^}]*producer: ownersAll, implicitDoor: true/);
    expect(producers.find((p) => p.name === "ownersAll")).toBeDefined();
  });
});
