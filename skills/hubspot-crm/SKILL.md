---
name: hubspot-crm
description: HubSpot CRM — contacts, companies, deals, tickets, pipelines, owners, associations. Activate BEFORE any HubSpot call; it returns the namespaces and rules to follow.
---

# HubSpot CRM Workflows

## Namespace

Calls go through `gateway.hubspot.<method>(args)` from inside `execute_javascript`
or `execute_python`. Never call them as top-level tools.

Methods are camelCase, args are camelCase too (HubSpot's own convention — different
from GitHub). Object types are passed as a string: `"contacts" | "companies" | "deals" | "tickets"`.
The same endpoints work for engagements (`"notes"`, `"tasks"`, `"calls"`, `"emails"`, `"meetings"`)
and custom object names.

If a call returns `gateway.hubspot.foo is not a workspace tool`, the error lists every valid
method — pick from it. Never re-send the same call.

## Cardinal rules

1. **Don't fabricate IDs.** Object IDs, owner IDs, pipeline/stage IDs — every one comes from a tool result.
2. **`properties` is `Map<String, String>`.** Even numeric fields (`amount`, `hs_object_id`) and dates come back as strings.
3. **Search before update.** HubSpot has no "lookup by email" endpoint — use `objectsSearch` with a property filter.
4. **Cap pagination.** Default `limit: 100` (the max). For "how many" use `objectsSearch` with `limit: 1` and read `total`.
5. **Confirm writes with the user.** `objectsCreate`, `objectsUpdate`, `objectsArchive` mutate live CRM data.

## Finding things

**Get by ID** — direct, cheap:

```javascript
const c = await gateway.hubspot.objectsGet({
  objectType: "contacts",
  objectId: "12345",
  properties: ["email", "firstname", "lastname", "company"],   // omit → returns default props only
});
console.log(c.properties.email);
```

**Find by property** (email, domain, name, custom field) — always uses `objectsSearch`:

```javascript
const r = await gateway.hubspot.objectsSearch({
  objectType: "contacts",
  filterGroups: [{ filters: [{ propertyName: "email", operator: "EQ", value: "alice@acme.com" }] }],
  properties: ["email", "firstname", "lastname", "hs_object_id"],
  limit: 1,
});
const contact = r.results[0];   // undefined if no match
```

**Counting** — `total` is in the search response. Don't list-and-count:

```javascript
const r = await gateway.hubspot.objectsSearch({
  objectType: "deals",
  filterGroups: [{ filters: [{ propertyName: "dealstage", operator: "EQ", value: "closedwon" }] }],
  limit: 1,
});
console.log(`Closed-won deals: ${r.total}`);
```

**Filter operators** — `EQ NEQ LT LTE GT GTE BETWEEN IN NOT_IN HAS_PROPERTY NOT_HAS_PROPERTY CONTAINS_TOKEN NOT_CONTAINS_TOKEN`.

**Multiple filter groups** are OR'd; filters within a group are AND'd. So `"deals owned by Alice OR Bob, value > 10k"` is two filter groups.

## Listing

```javascript
const r = await gateway.hubspot.objectsList({
  objectType: "deals",
  limit: 100,
  properties: ["dealname", "amount", "dealstage", "closedate"],
  archived: false,
});
// r is { results: [...], paging?: { next: { after: "...", link: "..." } } }
for (const d of r.results) console.log(d.id, d.properties.dealname);
```

Paginate with `after`:

```javascript
let after = undefined, all = [];
for (let page = 0; page < 5; page++) {                // hard cap
  const r = await gateway.hubspot.objectsList({ objectType: "contacts", limit: 100, after });
  all.push(...r.results);
  after = r.paging?.next?.after;
  if (!after) break;
}
```

## Creating

```javascript
const c = await gateway.hubspot.objectsCreate({
  objectType: "contacts",
  properties: {                     // strings only — even numbers/dates
    email: "alice@acme.com",
    firstname: "Alice",
    lastname: "Nguyen",
    company: "Acme",
  },
});
console.log(`Created contact ${c.id}`);
```

For deals, include `pipeline` and `dealstage` — get valid IDs from `pipelinesList`:

```javascript
const pipes = await gateway.hubspot.pipelinesList({ objectType: "deals" });
const sales = pipes.results.find(p => p.label === "Sales Pipeline");
const newStage = sales.stages.find(s => s.label === "New").id;

await gateway.hubspot.objectsCreate({
  objectType: "deals",
  properties: { dealname: "Acme — Q2 expansion", amount: "25000", pipeline: sales.id, dealstage: newStage },
});
```

## Updating

```javascript
await gateway.hubspot.objectsUpdate({
  objectType: "contacts",
  objectId: "12345",
  properties: { phone: "+1-415-555-0199", lifecyclestage: "customer" },
});
```

Only the properties you pass are touched. `objectsUpdate` returns the updated object.

## Archiving

```javascript
await gateway.hubspot.objectsArchive({ objectType: "contacts", objectId: "12345" });
// 204 No Content. The object is soft-deleted; pass `archived: true` to objectsList to see it.
```

## Owners

Used for assigning records to a specific HubSpot user. Owner IDs are NOT email addresses.

```javascript
const r = await gateway.hubspot.ownersList({ limit: 100 });
const alice = r.results.find(o => o.email === "alice@acme.com");
// then: properties: { hubspot_owner_id: String(alice.id) }
```

## Pipelines

Lists deal/ticket pipelines and their stages. Stage IDs are required for any
deal/ticket create/update that touches `dealstage`/`hs_pipeline_stage`.

```javascript
const pipes = await gateway.hubspot.pipelinesList({ objectType: "deals" });
for (const p of pipes.results) {
  console.log(`${p.label} (${p.id})`);
  for (const s of p.stages) console.log(`  → ${s.label} (${s.id})`);
}
```

## Associations (v4)

Linking a contact to a company, a deal to a contact, etc. Use the v4 endpoints —
v3 is deprecated for new work.

**Read existing associations:**

```javascript
const r = await gateway.hubspot.associationsGet({
  fromObjectType: "contacts",
  fromObjectId: "12345",
  toObjectType: "companies",
});
for (const a of r.results) console.log(`Linked to company ${a.toObjectId}`);
```

**Create a default association** (HubSpot picks the standard relationship type):

```javascript
await gateway.hubspot.associationsCreateDefault({
  fromObjectType: "contacts",
  fromObjectId: "12345",
  toObjectType: "companies",
  toObjectId: "67890",
});
```

For non-default association types (e.g. a custom "Decision Maker" link), pass
`associationTypes: [{ associationCategory: "USER_DEFINED", associationTypeId: 42 }]`
to `associationsCreate` (advanced — rarely needed in chat).

## Pitfalls

- Method names are **camelCase**; args are **camelCase**. HubSpot is consistent on both.
- `properties` always returns **strings** — `parseFloat(d.properties.amount)` if you need a number.
- Empty `properties` request → only `hs_object_id`, `createdate`, `lastmodifieddate` come back. Always pass the property list you need.
- **Object type strings are plural** for standard objects (`contacts`, not `contact`).
- `dealstage` / `hs_pipeline_stage` need a stage **ID** (e.g. `"appointmentscheduled"`), not the label.
- Owner ID is a **string**, even though it looks numeric. Wrap with `String(...)` when assigning.
- Search returns `{ total, results, paging }`; list returns `{ results, paging }` — no `total` on bare list.
- Rate limit (Private App): 100 req/10 sec. Don't hammer in tight loops — batch via `objectsSearch` instead.
