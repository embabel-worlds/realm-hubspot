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

// The fields a producer's `args.properties` projects, flow (`[a, b]`) or block (`- a`) form, comments dropped.
const projected = (block: string): string[] => {
  const yml = block.replace(/#.*$/gm, "");
  const flow = yml.match(/^ {4}properties: \[([^\]]*)\]/m);
  if (flow) return flow[1].split(",").map((f) => f.trim()).filter(Boolean);
  const list = yml.match(/^ {4}properties:\s*\n((?: {6}- .*\n?)+)/m);
  return list ? list[1].split("\n").map((l) => l.replace(/^\s*- /, "").trim()).filter(Boolean) : [];
};

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

  // A search filtered on an association does not say which anchor a record matched, so the producer must
  // echo the key, and the join must match on that echo; otherwise every record attaches to no anchor.
  it("every join over an association producer matches on the key that producer echoes", () => {
    const yml = typesYml.replace(/#.*$/gm, "");
    const joins = [...yml.matchAll(/recordKeyField: (\w+),?\s+producer: (\w+)/g)].map((m) => ({ key: m[1], producer: m[2] }));
    // Every join is seen, inline or block form, so a join the pattern misses fails here instead of passing unchecked.
    expect(joins.length).toBe((yml.match(/recordKeyField:/g) ?? []).length);
    const broken = joins.filter((j) => {
      const block = producers.find((p) => p.name === j.producer)?.block;
      if (block === undefined) return true;
      return /propertyName: associations\./.test(block) && block.match(/^ {2}echoKeyAs: (\S+)$/m)?.[1] !== j.key;
    });
    expect(broken).toEqual([]);
  });

  // "Who is on this deal / who raised this ticket" starts from the record, not from its company.
  it("a deal and a ticket each reach their own associated contacts", () => {
    const contact = typesYml.split(/^- name: /m).find((b: string) => b.startsWith("HubSpotContact\n")) ?? "";
    for (const [anchor, assoc] of [["HubSpotDeal", "deal"], ["HubSpotTicket", "ticket"]]) {
      const join = contact.match(new RegExp(`anchorLabel: ${anchor}, relationship: HAS_CONTACT,[^}]*producer: (\\w+)`));
      expect(join, anchor).not.toBeNull();
      const block = producers.find((p) => p.name === join![1])?.block ?? "";
      expect(block).toMatch(/^ {4}objectType: contacts$/m);
      expect(block).toMatch(new RegExp(`propertyName: associations\\.${assoc}$`, "m"));
    }
  });

  // An amount without its currency reads as a bare number, and the answer has to guess or disown the currency.
  it("every producer that projects a deal amount projects its currency code with it", () => {
    const withAmount = producers.filter((p) => projected(p.block).includes("amount"));
    expect(withAmount.length).toBeGreaterThan(0);
    const missing = withAmount.filter((p) => !projected(p.block).includes("deal_currency_code")).map((p) => p.name);
    expect(missing).toEqual([]);
  });

  // A record that carries its primary company's id reaches that company directly, not by a sweep of every company.
  it("a deal and a ticket each reach their primary company by the id they carry", () => {
    const company = typesYml.split(/^- name: /m).find((b: string) => b.startsWith("HubSpotCompany\n")) ?? "";
    for (const [anchor, key, producerList] of [["HubSpotDeal", "hs_primary_associated_company", "deals"], ["HubSpotTicket", "hs_primary_company_id", "tickets"]]) {
      const join = company.match(new RegExp(`anchorLabel: ${anchor}, relationship: HAS_PRIMARY_COMPANY, keyField: (\\w+),\\s+recordKeyField: hs_object_id, producer: (\\w+)`));
      expect(join, anchor).not.toBeNull();
      expect(join![1]).toBe(key);
      expect(producers.find((p) => p.name === join![2])?.block ?? "").toMatch(/propertyName: hs_object_id/);
      // Every producer of the anchor type projects the key, or the join has nothing to match on.
      const own = producers.filter((p) => new RegExp(`^ {4}objectType: ${producerList}$`, "m").test(p.block));
      expect(own.filter((p) => !projected(p.block).includes(key)).map((p) => p.name)).toEqual([]);
    }
  });

  // ownersList returns 100 owners a page; an unpaged read misses every later colleague.
  it("walks every page of the owner list", () => {
    for (const name of ["ownersAll", "ownersByEmail"]) {
      const block = producers.find((p) => p.name === name)?.block ?? "";
      expect(block, name).toMatch(/^ {2}paging: \{ style: cursor,.*cursorParam: after,.*cursorPath: "\$\.paging\.next\.after" \}$/m);
    }
  });
});
