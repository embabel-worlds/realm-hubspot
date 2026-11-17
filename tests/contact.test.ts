/**
 * Tests for the `HubSpotContact` verb class against a MOCKED gateway (no live
 * HubSpot, no key). `entityForTest` builds a real `HubSpotContact` with fields
 * set and the mock gateway injected — what the host does after virtual cypher
 * materializes the contact — so the verb runs unchanged. Pure verbs are asserted
 * by return value; effectful verbs by the `gateway.hubspot.*` ops + args.
 */
import { describe, it, expect, vi } from "vitest";
import { entityForTest, mockGateway } from "@embabel/runtime-types";
import type { GenericGatewayContext } from "@embabel/runtime-types";
import { HubSpotContact } from "../src/api/contact";

const NOW = Date.parse("2026-06-22T00:00:00Z");
const daysAgo = (n: number) => new Date(NOW - n * 86_400_000).toISOString();

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function contact(fields: Partial<HubSpotContact>, hubspot: Record<string, (args: any) => any> = {}) {
  return entityForTest(
    HubSpotContact,
    { email: "dana@example.com", ...fields },
    mockGateway<GenericGatewayContext>({ hubspot }),
  );
}

describe("HubSpotContact pure verbs", () => {
  it("fullName from first/last, falling back to email", () => {
    expect(contact({ firstname: "Dana", lastname: "Grey" }).fullName()).toBe("Dana Grey");
    expect(contact({}).fullName()).toBe("dana@example.com");
  });

  it("daysSinceContacted / isStale / needsFollowUp from notes_last_contacted", () => {
    expect(contact({ notes_last_contacted: daysAgo(40) }).daysSinceContacted(NOW)).toBe(40);
    expect(contact({ notes_last_contacted: daysAgo(40) }).isStale(30, NOW)).toBe(true);
    expect(contact({ notes_last_contacted: daysAgo(5) }).isStale(30, NOW)).toBe(false);
    expect(contact({}).isStale(30, NOW)).toBe(true);              // never contacted
    expect(contact({ notes_last_contacted: daysAgo(20) }).needsFollowUp(14, NOW)).toBe(true);
  });
});

describe("HubSpotContact effectful verbs", () => {
  it("logNote resolves the contact id, creates the note, and associates it", async () => {
    const objectsGet = vi.fn(async () => ({ id: "501" }));
    const notesCreate = vi.fn(async () => ({ id: "note-1" }));
    const associationsCreateDefault = vi.fn(async () => ({}));
    await contact({}, { objectsGet, notesCreate, associationsCreateDefault }).logNote("Followed up re: renewal", NOW);

    expect(objectsGet).toHaveBeenCalledWith({ objectType: "contacts", objectId: "dana@example.com", idProperty: "email" });
    expect(notesCreate).toHaveBeenCalledWith(expect.objectContaining({
      properties: expect.objectContaining({ hs_note_body: "Followed up re: renewal" }),
    }));
    expect(associationsCreateDefault).toHaveBeenCalledWith({
      fromObjectType: "notes", fromObjectId: "note-1", toObjectType: "contacts", toObjectId: "501",
    });
  });

  it("createFollowUpTask creates a task and associates it to the contact", async () => {
    const objectsGet = vi.fn(async () => ({ id: "501" }));
    const tasksCreate = vi.fn(async () => ({ id: "task-1" }));
    const associationsCreateDefault = vi.fn(async () => ({}));
    await contact({}, { objectsGet, tasksCreate, associationsCreateDefault })
      .createFollowUpTask("Call Dana", "2026-07-01T09:00:00Z");

    expect(tasksCreate).toHaveBeenCalledWith(expect.objectContaining({
      properties: expect.objectContaining({ hs_task_subject: "Call Dana", hs_timestamp: "2026-07-01T09:00:00Z" }),
    }));
    expect(associationsCreateDefault).toHaveBeenCalledWith(expect.objectContaining({
      fromObjectType: "tasks", fromObjectId: "task-1", toObjectType: "contacts", toObjectId: "501",
    }));
  });
});
