"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.HubSpotCompany = void 0;
const runtime_types_1 = require("@embabel/runtime-types");
/**
 * A HubSpot CRM company materialized on demand (see `types/hubspot.yml`). Its graph
 * identity is the HubSpot object id (`hs_object_id`), so it dedupes whether reached by
 * domain (from an Organization) or by object id (from a contact's primary company).
 * The realm declares no operation that updates a company, so it has no write verb.
 */
class HubSpotCompany extends runtime_types_1.Entity {
    hs_object_id;
    domain;
    name;
    industry;
    website;
    /** Whether the company has a usable web domain (its link key to Organization and CustomerAccount). */
    hasDomain() {
        return !!this.domain && this.domain.trim().length > 0;
    }
}
exports.HubSpotCompany = HubSpotCompany;
