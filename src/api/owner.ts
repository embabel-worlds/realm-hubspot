import { Entity } from "@embabel/runtime-types";

/**
 * A HubSpot owner (CRM user/seller) materialized on demand (see
 * `types/hubspot.yml`), keyed by the numeric owner `id`. A read-side anchor —
 * its only verb is a pure display helper. A deal or task is assigned to an
 * owner through `dealUpdate` or `tasksCreate` (`hubspot_owner_id`).
 */
export class HubSpotOwner extends Entity {
  // `id` (the numeric owner id, identity) is inherited from Entity.
  email?: string;
  firstName?: string;
  lastName?: string;

  /** Best display name from first/last, falling back to the email or owner id. */
  displayName(): string {
    const name = [this.firstName, this.lastName].filter(Boolean).join(" ").trim();
    return name || this.email || this.id;
  }
}
