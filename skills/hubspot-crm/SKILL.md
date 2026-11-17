---
name: hubspot-crm
description: HubSpot CRM — contacts, companies, deals, tickets, activities (calls, emails, meetings, notes, tasks), pipelines, owners, associations. Activate BEFORE any HubSpot call; it returns the namespaces and rules to follow.
---

# HubSpot CRM Workflows

## Asking the right question

HubSpot's date properties answer narrower questions than their names suggest. Pick the one
that means what the user asked:

| The user asks | Read | Why |
|---|---|---|
| "last contacted", "gone quiet", "no touch in 30 days" | `notes_last_contacted` on the contact, company or deal | The last time WE reached them: a sent email, a call, a meeting. An email THEY sent us does not move it (measured on a live portal), and neither does a note. |
| "not emailed in N days" | the contact's `HubSpotEmail` activities with `hs_email_direction = 'EMAIL'` (sent by us) | `notes_last_contacted` also counts calls and meetings, so it answers a different question. |
| "past-due follow-ups", "overdue tasks" | `HubSpotTask` with `hs_task_status <> 'COMPLETED'` and `hs_timestamp` before today | A task's `hs_timestamp` is its DUE date. |
| "what's next", "anything scheduled" | `notes_next_activity_date` | Shows only UPCOMING activity. An overdue task is not in it, so an empty value never means "nothing outstanding". |
| "any activity at all" | `notes_last_activity_date` | Every kind, inbound and notes included. |
| "stalled deal" | open deal (`hs_is_closed = 'false'`) whose `notes_last_contacted` is 45 or more days before today | No email, call or meeting from us in 45 days. Not `hs_lastmodifieddate`: HubSpot stamps it at every write, so it says when the record changed, not when anyone talked. |
| "slipping deal" | open deal whose `closedate` is before today | The close date passed and the deal is still open. |
| "open tickets" | `HubSpotTicket` with `hs_is_closed = 'false'` | Stage ids differ by pipeline; `hs_is_closed` does not. |

"Today" is the current date, or the date the conversation gives as today. Work cut-offs out from it and compare ISO timestamps lexically,
and say which date you took as today:

```javascript
const today = new Date().toISOString().slice(0, 10);                          // "YYYY-MM-DD", unless given
const daysBefore = (n) => new Date(Date.parse(today) - n * 86_400_000).toISOString().slice(0, 10);
const dayOf = (iso) => iso.slice(0, 10);                                       // a timestamp's date
const last = (deal) => deal.properties.notes_last_contacted;                   // absent: never contacted
const stalled = (deal) => !last(deal) || dayOf(last(deal)) <= daysBefore(45);
```

Compare a timestamp's date (its first 10 characters) with a cut-off, or a timestamp exceeds its
own day's cut-off. In the queries below, `<today>` stands for today's date as `YYYY-MM-DD` and
`<today-45d>` for the date 45 days before it.

## Through the knowledge graph

Every HubSpot record type is virtual: a Cypher query that traverses to it fetches it live.

```cypher
// Dana's stalled deals: open, nobody reached in 45 days
MATCH (p:Person) WHERE toLower(p.name) CONTAINS 'dana'
MATCH (p)-[:HAS_HUBSPOT_OWNER]->(:HubSpotOwner)-[:OWNS_DEAL]->(d:HubSpotDeal)
WHERE d.hs_is_closed = 'false' AND substring(d.notes_last_contacted, 0, 10) <= '<today-45d>'
RETURN d.dealname, d.dealstage, d.closedate, d.notes_last_contacted

// My past-due follow-ups
MATCH (me:AssistantUser)-[:HAS_HUBSPOT_OWNER]->(:HubSpotOwner)-[:OWNS_ACTIVITY]->(t:HubSpotTask)
WHERE t.hs_task_status <> 'COMPLETED' AND t.hs_timestamp < '<today>'
RETURN t.hs_task_subject, t.hs_timestamp

// An account's open tickets
MATCH (b:HubSpotBook {scope:'all'})-[:HAS_COMPANY]->(co:HubSpotCompany {name:'Example Co'})-[:HAS_TICKET]->(t:HubSpotTicket)
WHERE t.hs_is_closed = 'false'
RETURN t.subject, t.hs_ticket_priority, t.createdate
```

| From | Edge | To |
|---|---|---|
| `AssistantUser`, `Person` | `HAS_HUBSPOT_OWNER` | `HubSpotOwner` |
| `HubSpotOwner` | `OWNS_CONTACT`, `OWNS_DEAL`, `OWNS_TICKET`, `OWNS_ACTIVITY` | contacts, deals, tickets, activities |
| `HubSpotBook {scope:'all'}` | `HAS_COMPANY`, `HAS_DEAL` | every company, every deal |
| `HubSpotCompany` | `HAS_CONTACT`, `HAS_DEAL`, `HAS_TICKET` | by the record's PRIMARY company |
| `HubSpotContact` | `HAS_PRIMARY_COMPANY`, `HAS_DEAL`, `HAS_TICKET` | |
| `HubSpotContact`, `HubSpotCompany`, `HubSpotDeal` | `HAS_ACTIVITY` | `HubSpotActivity` (any kind) or a kind: `HubSpotCall`, `HubSpotEmail`, `HubSpotMeeting`, `HubSpotNote`, `HubSpotTask` |
| `CustomerAccount` | `TRACKED_AS` | `HubSpotCompany`, by domain |

`dealstage`, `pipeline` and `hs_pipeline_stage` hold IDs; map them to labels with `pipelinesList`.

## Namespace

Calls go through `gateway.hubspot.<method>(args)` from inside `execute_javascript`
or `execute_python`. Never call them as top-level tools.

Methods are camelCase, args are camelCase too (HubSpot's own convention — different
from GitHub). Object types are passed as a string: `"contacts" | "companies" | "deals" | "tickets"`.
The same endpoints work for engagements (`"notes"`, `"tasks"`, `"calls"`, `"emails"`, `"meetings"`)
and custom object names.

If a call returns `gateway.hubspot.foo is not a world tool`, the error lists every valid
method — pick from it. Never re-send the same call.

## Cardinal rules

1. **Don't fabricate IDs.** Object IDs, owner IDs, pipeline/stage IDs — every one comes from a tool result.
2. **`properties` is `Map<String, String>`.** Even numeric fields (`amount`, `hs_object_id`) and dates come back as strings.
3. **Search before you write.** HubSpot has no "lookup by email" endpoint — use `objectsSearch` with a property filter to find the id.
4. **Cap pagination.** Default `limit: 100` (the max). For "how many" use `objectsSearch` with `limit: 1` and read `total`.
5. **Writes are few and narrow.** You can log a note, create a task, attach either to a record, update a deal's properties, and move a deal's stage. Nothing deletes, and nothing creates or updates a contact or company: say so when asked, rather than looking for a way round.

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
  filterGroups: [{ filters: [{ propertyName: "email", operator: "EQ", value: "alice@example.com" }] }],
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

## Writing

Five writes, nothing else. Each changes live CRM data.

**Log a note, then attach it** to a contact, company, deal or ticket:

```javascript
const note = await gateway.hubspot.notesCreate({
  properties: { hs_note_body: "Called about the renewal; they want a quote by Friday.", hs_timestamp: new Date().toISOString() },
});
await gateway.hubspot.notesAssociate({ noteId: note.id, toObjectType: "contacts", toObjectId: "12345" });
```

**Create a follow-up task** (`hs_timestamp` is the DUE date; `hs_task_body` is required, so repeat the subject when there is nothing more to say), then attach it with `tasksAssociate`:

```javascript
const task = await gateway.hubspot.tasksCreate({
  properties: {
    hs_task_subject: "Send the renewal quote", hs_task_body: "They asked for it by Friday.",
    hs_timestamp: new Date(Date.now() + 4 * 86_400_000).toISOString(), hs_task_type: "EMAIL", hubspot_owner_id: "67890",
  },
});
await gateway.hubspot.tasksAssociate({ taskId: task.id, toObjectType: "contacts", toObjectId: "12345" });
```

**Update a deal's properties** — `dealname`, `amount`, `closedate`, `hubspot_owner_id`, `hs_next_step`, `description`, `hs_priority`, `dealtype`, and no others:

```javascript
await gateway.hubspot.dealUpdate({ dealId: "12345", properties: { amount: "25000", closedate: "2026-12-15" } });
```

**Move a deal to another stage** — only through `dealMoveStage`. `dealUpdate` does not take `dealstage` or `pipeline`. A stage move is sensitive: the user is asked to approve every one before it runs, so say what you are about to move and why. Take the stage ID from `pipelinesList`:

```javascript
const pipes = await gateway.hubspot.pipelinesList({ objectType: "deals" });
const stage = pipes.results.find(p => p.id === "default").stages.find(s => s.label === "Contract sent").id;
await gateway.hubspot.dealMoveStage({ dealId: "12345", properties: { dealstage: stage } });
```

## Owners

Used for assigning records to a specific HubSpot user. Owner IDs are NOT email addresses.

```javascript
const r = await gateway.hubspot.ownersList({ limit: 100 });
const alice = r.results.find(o => o.email === "alice@example.com");
// then, on a deal or task: properties: { hubspot_owner_id: String(alice.id) }
```

## Pipelines

Lists deal/ticket pipelines and their stages. `dealstage` and `hs_pipeline_stage`
hold stage IDs; map them to labels here, and take the target ID from here for `dealMoveStage`.

```javascript
const pipes = await gateway.hubspot.pipelinesList({ objectType: "deals" });
for (const p of pipes.results) {
  console.log(`${p.label} (${p.id})`);
  for (const s of p.stages) console.log(`  → ${s.label} (${s.id})`);
}
```

## Associations (v4)

**Read existing associations:**

```javascript
const r = await gateway.hubspot.associationsGet({
  fromObjectType: "contacts",
  fromObjectId: "12345",
  toObjectType: "companies",
});
for (const a of r.results) console.log(`Linked to company ${a.toObjectId}`);
```

`notesAssociate` and `tasksAssociate` attach a note or a task to a record (see Writing). Nothing
links contacts, companies or deals to each other.

## Pitfalls

- Method names are **camelCase**; args are **camelCase**. HubSpot is consistent on both.
- `properties` always returns **strings** — `parseFloat(d.properties.amount)` if you need a number.
- Empty `properties` request → only `hs_object_id`, `createdate`, `lastmodifieddate` come back. Always pass the property list you need.
- **Object type strings are plural** for standard objects (`contacts`, not `contact`).
- `dealstage` / `hs_pipeline_stage` need a stage **ID** (e.g. `"appointmentscheduled"`), not the label.
- Owner ID is a **string**, even though it looks numeric. Wrap with `String(...)` when assigning.
- Search returns `{ total, results, paging }`; list returns `{ results, paging }` — no `total` on bare list.
- Rate limit: about 100 requests per 10 seconds per key. Don't hammer in tight loops — batch via `objectsSearch` instead.
