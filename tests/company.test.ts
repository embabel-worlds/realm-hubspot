import { describe, it, expect } from "vitest";
import { entityForTest, mockGateway } from "@embabel/runtime-types";
import type { GenericGatewayContext } from "@embabel/runtime-types";
import { HubSpotCompany } from "../src/api/company";

function company(fields: Partial<HubSpotCompany>) {
  return entityForTest(
    HubSpotCompany,
    { domain: "example.com", ...fields },
    mockGateway<GenericGatewayContext>({ hubspot: {} }),
  );
}

describe("HubSpotCompany", () => {
  it("hasDomain says whether the company has a domain to link by", () => {
    expect(company({}).hasDomain()).toBe(true);
    expect(company({ domain: "" }).hasDomain()).toBe(false);
  });
});
