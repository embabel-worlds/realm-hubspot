"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.HubSpotOwner = void 0;
const runtime_types_1 = require("@embabel/runtime-types");
/**
 * A HubSpot owner (CRM user/seller) materialized on demand (see
 * `types/hubspot.yml`), keyed by the numeric owner `id`. A read-side anchor —
 * its only verb is a pure display helper. A deal or task is assigned to an
 * owner through `dealUpdate` or `tasksCreate` (`hubspot_owner_id`).
 */
class HubSpotOwner extends runtime_types_1.Entity {
    // `id` (the numeric owner id, identity) is inherited from Entity.
    email;
    firstName;
    lastName;
    /** Best display name from first/last, falling back to the email or owner id. */
    displayName() {
        const name = [this.firstName, this.lastName].filter(Boolean).join(" ").trim();
        return name || this.email || this.id;
    }
}
exports.HubSpotOwner = HubSpotOwner;
