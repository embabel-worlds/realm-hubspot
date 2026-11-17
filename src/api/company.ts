import { Entity } from "@embabel/runtime-types";

/**
 * A HubSpot CRM company materialized on demand (see `types/hubspot.yml`). Its graph
 * identity is the HubSpot object id (`hs_object_id`), so it dedupes whether reached by
 * domain (from an Organization) or by object id (from a contact's primary company).
 * The realm declares no operation that updates a company, so it has no write verb.
 */
export class HubSpotCompany extends Entity {
  hs_object_id?: string;
  domain!: string;
  name?: string;
  industry?: string;
  website?: string;

  /** Whether the company has a usable web domain (its link key to Organization and CustomerAccount). */
  hasDomain(): boolean {
    return !!this.domain && this.domain.trim().length > 0;
  }
}
