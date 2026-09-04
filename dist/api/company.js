"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.HubSpotCompany = void 0;
const runtime_types_1 = require("@embabel/runtime-types");
/**
 * A HubSpot CRM company materialized on demand (see `types/hubspot.yml`). Its graph
 * identity is the HubSpot object id (`hs_object_id`), so it dedupes whether reached by
 * domain (from an Organization) or by object id (from a contact's primary company).
 * `update` still writes back addressing the company by `domain` via `idProperty` (a
 * convenient natural key — no numeric-id lookup); it requires a usable domain.
 */
class HubSpotCompany extends runtime_types_1.Entity {
    // `id` (identity) is the HubSpot object id; `domain` is the natural key used for write-back.
    hs_object_id;
    domain;
    name;
    industry;
    website;
    get api() {
        return this.gateway;
    }
    /** Whether the company has a usable web domain (its identity / update key). */
    hasDomain() {
        return !!this.domain && this.domain.trim().length > 0;
    }
    /** Update company properties (addressed by domain). */
    async update(properties) {
        if (!this.hasDomain())
            throw new Error("cannot update a HubSpot company with no domain");
        return this.api.hubspot.objectsUpdate({ objectType: "companies", objectId: this.domain, idProperty: "domain", properties });
    }
}
exports.HubSpotCompany = HubSpotCompany;
