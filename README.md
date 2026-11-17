# realm-hubspot

HubSpot CRM for an Embabel World: contacts, companies, deals, tickets and activities (calls,
emails, meetings, notes, tasks), read live through a curated, vendored OpenAPI spec, plus a
few narrow writes that each say what they change.

## What it gives the agent

- **Virtual types** (`types/hubspot.yml`, producers in `producers/hubspot.yml`): `HubSpotContact`,
  `HubSpotCompany`, `HubSpotOwner`, `HubSpotDeal`, `HubSpotTicket`, and `HubSpotActivity` with
  one child per kind (`HubSpotCall`, `HubSpotEmail`, `HubSpotMeeting`, `HubSpotNote`,
  `HubSpotTask`). Nothing is mirrored: a Cypher query that traverses to one fetches it.
- **The business vocabulary.** `HubSpotDeal` is an `Opportunity`, `HubSpotCompany` a
  `CrmAccount` and `HubSpotContact` an `AccountContact` of
  [realm-business-vocabulary](https://github.com/embabel-worlds/realm-business-vocabulary),
  which must be installed beside this realm. A company's domain keys the `CustomerAccount`
  spine, so HubSpot's record of an account meets the helpdesk's and billing's.
- **The `hubspot-crm` skill** (`skills/hubspot-crm/SKILL.md`): which HubSpot date answers which
  question, the graph's edges, and how to call each operation.
- **The `gateway.hubspot` namespace** (`apis/hubspot-crm.json`).

## Operations

Reads, generalised over object type (`contacts`, `companies`, `deals`, `tickets`, `notes`,
`tasks`, `calls`, `emails`, `meetings`): `objectsList`, `objectsGet`, `objectsSearch`,
`ownersList`, `ownersGet`, `pipelinesList`, `pipelinesListStages`, `associationsGet`.

Writes, each declared with `x-embabel-effect` in the spec and listed in `write-operation-ids`:

| Operation | Does | Tier |
|---|---|---|
| `notesCreate` | logs a note | Effect |
| `tasksCreate` | creates a follow-up task | Effect |
| `notesAssociate`, `tasksAssociate` | attach a note or task to a contact, company, deal or ticket; the source type is fixed in each path | Effect |
| `dealUpdate` | sets a deal's name, amount, close date, owner, next step, description, priority or type; never its stage or pipeline | Effect |
| `dealMoveStage` | moves a deal to another stage | Sensitive, `approval: always`: a person approves each call, in chat too |

Nothing deletes, and nothing creates or updates a contact or company. `apis/apis.yml` exposes
only the operations above. `tests/tiers.test.ts` checks the spec's shape: the list as the host
filters and classifies it, and that the spec declares no route around each rule.

## Setup

1. Install [realm-business-vocabulary](https://github.com/embabel-worlds/realm-business-vocabulary),
   then this realm.
2. In HubSpot, create a
   [Service Key](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/account-service-keys)
   (Development → Keys → Service keys) with these scopes:

   ```
   crm.objects.contacts.read   crm.objects.companies.read   crm.objects.owners.read
   crm.objects.deals.read      crm.objects.deals.write      crm.objects.tickets.read
   crm.objects.calls.read      crm.objects.emails.read      crm.objects.meetings.read
   crm.objects.notes.read      crm.objects.tasks.read       crm.objects.contacts.write
   ```

   HubSpot defines no notes or tasks write scope: it authorises creating one by the write scope of
   the record it belongs to, which is why `crm.objects.contacts.write` is there. The realm
   declares no operation that changes a contact.
3. Set the key as `HUBSPOT_SERVICE_KEY` on the realm's settings (`keys.yml`). The host checks
   it with `ownersList`.

Webhooks are not part of this realm: Service Keys receive none, and they need a HubSpot app
([#2](https://github.com/embabel-worlds/realm-hubspot/issues/2),
[#3](https://github.com/embabel-worlds/realm-hubspot/issues/3)).

## Sample queries

The HubSpot nodes are virtual, so keep the join in the leading `MATCH` chain and put
`WHERE`, `ORDER BY` and `LIMIT` at the end.

```cypher
// Your open deals and when anyone last reached them
MATCH (me:AssistantUser)-[:HAS_HUBSPOT_OWNER]->(:HubSpotOwner)-[:OWNS_DEAL]->(d:HubSpotDeal)
WHERE d.hs_is_closed = 'false'
RETURN d.dealname, d.dealstage, d.closedate, d.notes_last_contacted
```

```cypher
// Your past-due follow-ups (<today> is today's date, YYYY-MM-DD)
MATCH (me:AssistantUser)-[:HAS_HUBSPOT_OWNER]->(:HubSpotOwner)-[:OWNS_ACTIVITY]->(t:HubSpotTask)
WHERE t.hs_task_status <> 'COMPLETED' AND t.hs_timestamp < '<today>'
RETURN t.hs_task_subject, t.hs_timestamp
```

```cypher
// Every company's open tickets
MATCH (co:HubSpotCompany)-[:HAS_TICKET]->(t:HubSpotTicket)
WHERE t.hs_is_closed = 'false'
RETURN co.name, t.subject, t.hs_ticket_priority
```

```cypher
// Of the contacts you own, the five most worth an outreach about a security product
MATCH (me:AssistantUser)-[:HAS_HUBSPOT_OWNER]->(:HubSpotOwner)-[:OWNS_CONTACT]->(c:HubSpotContact)
WHERE c.notes_last_contacted < '2026-04-01'
RETURN c.email, c.firstname, c.lastname, c.jobtitle
ORDER BY ai.score(c, 'most likely to care about a security and compliance product') DESC LIMIT 5
```

## Why a vendored spec

HubSpot no longer publishes per-API OpenAPI specs at a stable URL, and community specs drift.
`apis/hubspot-crm.json` is a hand-written subset: the reads parameterised by object type, and
one narrow operation per write.

## Development

```bash
npm ci
npm run typecheck && npm test && npm run build   # build writes dist/, which is committed
```

`@embabel/runtime-types` resolves from a sibling `me` checkout (`../../me/tooling/runtime-types`).

`sh tests/verify.sh` checks the realm against a live portal and a running Me, read-only: the
stalled, slipping, gone-quiet, past-due and open-ticket sets through the producers must equal
HubSpot's own, and the served write surface must equal `apis/apis.yml`. Its header lists the
environment it needs.

## Not in this realm

Marketing emails, forms, CMS, files, property metadata, webhooks, and any write beyond the
five above.
