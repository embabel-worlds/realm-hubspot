# realm-hubspot

HubSpot CRM v3 via a curated, vendored OpenAPI 3 spec — gives the LLM
full request **and** response types for the high-traffic CRM surface
without depending on HubSpot's now-defunct public spec catalog.

> Realm authoring reference: see
> [`docs/realm-format.md`](https://github.com/embabel/assistant/blob/main/docs/realm-format.md)
> in the assistant repo for the full realm format spec — vendored
> OpenAPI specs, OAuth2, identity introspection, admin OAuth app
> registry, and per-world overrides are all documented there.

## Why

HubSpot used to publish per-API OpenAPI specs at
`api.hubspot.com/api-catalog-public/v1/apis/...`. Those URLs all 404 as
of 2026 — HubSpot's docs site is now SPA-rendered with no spec download
links, and apis.guru's `crm` mirror only contains the Cards extension.
Community-maintained unified specs exist but drift.

This realm vendors a hand-written mini-spec (`apis/hubspot-crm.json`)
that covers ~12 ops parameterised by object type, handling 95% of
chat-driven CRM workflows. Same data the LLM would read from a
3,000-op upstream dump, in a tenth of the prompt.

## Namespace

Methods land under `gateway.hubspot`. E.g.:

- `gateway.hubspot.objectsList({ objectType: "contacts", limit: 100 })`
- `gateway.hubspot.objectsGet({ objectType: "deals", objectId: "12345" })`
- `gateway.hubspot.objectsCreate({ objectType: "contacts", properties: { email, firstname, lastname } })`
- `gateway.hubspot.objectsSearch({ objectType: "deals", filterGroups: [...] })`
- `gateway.hubspot.ownersList({})`
- `gateway.hubspot.pipelinesList({ objectType: "deals" })`
- `gateway.hubspot.associationsGet({ fromObjectType: "contacts", fromObjectId, toObjectType: "deals" })`

See `prompts/examples.md` for usage patterns.

## Auth — OAuth2

End users **never** paste API tokens, never know about client IDs, and
never set environment variables. They click **Authorize** in
Settings → Connected Services. That's it.

This works because the assistant deployment has ONE registered HubSpot
Public App. Every end user connects their own HubSpot account against
that single app — same as how "Login with Google" works on every
website you've ever used.

### For end users

1. Open **Settings → Connected Services**.
2. Click **Authorize** on the `hubspot` row.
3. Consent on HubSpot's page. Done — `gateway.hubspot.*` is live in chat.

If the row shows **"Not configured"**, the deployment operator hasn't
registered the HubSpot app yet — show them the next section.

### For installation admins (one-time setup)

Done once per installation. Every world in the installation
inherits — end users just click Authorize.

1. **Create a HubSpot Public App** at
   `app.hubspot.com/developer/<your-hubid>/applications`.
2. **Scopes** — enable at least:
   ```
   crm.objects.contacts.read   crm.objects.contacts.write
   crm.objects.companies.read  crm.objects.companies.write
   crm.objects.deals.read      crm.objects.deals.write
   crm.objects.owners.read     tickets
   ```
   Pare back to read-only if the installation should only browse,
   never write.
3. **Redirect URI** — set to your assistant's public callback URL:
   `https://your-host/api/v1/auth/oauth2/callback`
   (or `http://localhost:8042/api/v1/auth/oauth2/callback` for local
   dev).
4. **Copy** the app's client ID and client secret.
5. **Add them to** `{worldBase}/admin/oauth-apps.yml` (the same
   admin directory that holds `realm-sources.yml`, `themes/`, `hints/`,
   etc.):

   ```yaml
   apps:
     hubspot:
       client-id: 12345-abcdef-...
       client-secret: secret-blah
   ```

   Hot-reloaded — no restart needed. Every world in the
   installation will see "Authorize" appear in Settings.

A specific world can opt out of the installation default and
point at its own HubSpot app by writing the same shape to
`<world>/config/oauth-apps.yml` — useful if one team needs a
different brand on the consent screen.

Token refresh is automatic. End users can disconnect from the same
Settings panel any time.

### Webhook setup (one-time, operator only)

The realm ships a `webhooks/contact-creation.yml` registration that
declares signature verification + tenancy. **The actual subscription
on HubSpot's side has to be created once per installation** (HubSpot
exposes app-webhook configuration only via API, not the dev portal UI
for newer Public Apps).

A helper script does it. **Run it from the root of this realm** — wherever it lives on the operator's machine. For a realm installed into a world that's typically:

```bash
cd ~/embabel/assistant/<your-username>/<your-world>/config/realms/realm-hubspot
git pull   # if it's been a while
```

Two ways to invoke:

**Recommended — short form.** Add `app-id` and `developer-key` to your existing `apps.hubspot` block in `admin/oauth-apps.yml` (the same file that already holds `client-id` / `client-secret`):

```yaml
apps:
  hubspot:
    client-id:    "..."
    client-secret: "..."
    app-id:       "678910"          # ← add
    developer-key: "hapi-xxx-xxx"   # ← add
```

Then:

```bash
scripts/register-webhook.sh <public-base-url>
```

The script reads `app-id` and `developer-key` from `oauth-apps.yml` (admin file by default; `<world>/config/oauth-apps.yml` as fallback).

**Override — long form.** Pass all three explicitly to register against a different app without editing YAML:

```bash
scripts/register-webhook.sh <public-base-url> <app-id> <developer-hapikey>
```

### Where to find each value

| Field | Where to find it |
|---|---|
| `app-id` | Open your app in the developer portal. The URL is `https://app.hubspot.com/developer/<account-id>/applications/<app-id>` — the **numeric id after `/applications/`**. Six-to-eight digits. Public, not secret. |
| `developer-key` | In your **Developer Account** (not your CRM portal): click the **⚙ Settings icon** (top nav) → **Integrations → API key** (left sidebar) → **Show key** or **Generate**. Super Admin permission required. ⚠️ This is the *Developer* API Key, **not** the (deprecated) standard HubSpot API key, and **not** the OAuth Client Secret — three different credentials. The Developer API key remains active despite the broader 2022 API-key deprecation, because it's what authenticates app-management endpoints like webhook subscriptions. |
| `<public-base-url>` | The public host your assistant accepts webhooks on, no trailing slash. E.g. `https://my-host.example.com` or your Tailscale Funnel hostname. The script appends `/api/v1/webhooks/hubspot`. |

If you have multiple HubSpot accounts (a CRM tenant plus a separate Developer Account hosting your apps), make sure you're logged into the **Developer Account** when looking up the API key — it's not in the CRM portal's settings.

Idempotent — re-running after a public-URL change updates the target URL without duplicating the subscription. Safe to re-run after any restart or admin change.

```bash
$ scripts/register-webhook.sh https://example.com
→ Using app-id and developer-key from /Users/rod/embabel/assistant/admin/oauth-apps.yml
→ Configuring webhook for app 678910
  target URL: https://example.com/api/v1/webhooks/hubspot
→ Setting webhook target URL …
  ✓ target URL set
→ Listing existing subscriptions …
→ Creating contact.creation subscription …
  ✓ created subscription id=42
```

End users never run this. Once the subscription exists, every user who
later authorizes via Settings → Connected Services contributes their
contacts to the same webhook stream — the assistant's `payload-field`
tenancy resolver routes each event to the right user via `portalId`.

## Object types covered

`contacts`, `companies`, `deals`, `tickets`. All share the same
`SimplePublicObject` request/response shape — `properties` is a
`Map<String, String>`, IDs are strings, timestamps are ISO 8601.

For non-default objects (line items, products, custom objects) the same
endpoints apply — pass the object's API name as `objectType`.

## Sample queries

Run these in the Cypher console (Settings → Data → **Query**). The HubSpot nodes are
virtual — each materializes on demand when a query traverses to it — so keep the virtual
join in the leading `MATCH` chain and put `WHERE`/`ORDER BY`/`LIMIT` at the end. A concrete
field (a lead status, a `notes_last_contacted` date) belongs in an ordinary `WHERE`; the
`ai.*` primitives below are only for a subjective read no stored property captures.

**Whose contacts (owner-scoped)**
```cypher
-- The contacts you own
MATCH (me:AssistantUser)-[:HAS_HUBSPOT_OWNER]->(:HubSpotOwner)-[:OWNS_CONTACT]->(c:HubSpotContact)
RETURN c.email, c.firstname, c.lastname, c.jobtitle
```

**LLM-judged (the `ai` namespace — filter or rank on a subjective read of a fetched contact)**

The `ai` namespace is reserved for per-row LLM judgment over rows a virtual join has fetched,
for a discriminator no stored property or embedding captures. `ai.relevant(...)` *filters* (keeping
the contact); `ai.score(...)` *reranks*. A field like `hs_lead_status` or a date stays an
ordinary `WHERE`.
```cypher
-- Of the contacts you own, the ones who read like senior decision-makers at sizeable companies
MATCH (me:AssistantUser)-[:HAS_HUBSPOT_OWNER]->(:HubSpotOwner)-[:OWNS_CONTACT]->(c:HubSpotContact)
WHERE ai.relevant(c, 'a senior decision-maker at a sizeable company — not a junior individual contributor or a one-person shop')
RETURN c.email, c.firstname, c.lastname, c.jobtitle
```
```cypher
-- Your five contacts most worth an outreach about a security-and-compliance product, skipping anyone contacted since spring
MATCH (me:AssistantUser)-[:HAS_HUBSPOT_OWNER]->(:HubSpotOwner)-[:OWNS_CONTACT]->(c:HubSpotContact)
WHERE c.notes_last_contacted < '2026-04-01'
RETURN c.email, c.firstname, c.lastname, c.jobtitle
ORDER BY ai.score(c, 'most likely to care about a security and compliance product — role, seniority and company all point that way') DESC LIMIT 5
```

## What's NOT in this realm

To keep the spec small, these are out of scope:

- **Marketing emails / forms / CTAs** — different API surface, add a
  separate spec if needed
- **CMS / blog / hubdb** — content APIs, different shapes
- **Webhooks subscriptions** — out-of-band; configure in HubSpot UI
- **Files API** — separate; can be added later
- **Properties metadata** — listing custom properties; rarely needed in
  chat (just pass the property names you know)
- **Engagements (notes/tasks/calls/emails/meetings)** — these are
  separate object types under `/crm/v3/objects/{type}` and work via the
  same generic endpoints; pass `objectType: "notes"` etc.

## Sample data

Spin up a free HubSpot developer test account at
`app.hubspot.com/signup-hubspot/developers` — enables sample contacts,
companies, and deals on first login. Good enough to verify the realm.
