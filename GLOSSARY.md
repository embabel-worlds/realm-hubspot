# HubSpot Realm

The words this realm uses for HubSpot CRM data as an Embabel World sees it, and for how the realm is evaluated.

## Language

**Portal**:
One HubSpot account, identified by its portal id (hub id); all CRM records live in exactly one Portal.
_Avoid_: hub, account, instance, tenant

**Source Portal**:
The Portal whose real data is profiled for its shape; nothing from it is copied as content.
_Avoid_: production portal, real HubSpot

**Test Portal**:
A disposable Portal holding only synthetic data, the only Portal evals and seeding ever write to.
_Avoid_: sandbox, demo portal

**Shape Profile**:
Aggregate statistics and schema of a Source Portal: counts, fill rates, pipelines, stages, distributions, associations; never records.
_Avoid_: sample data, export, snapshot

**Book**:
The one shared fictional business story every CRM, helpdesk and billing realm is seeded from, pinned by tag; the single source of eval truth.
_Avoid_: seed data, demo data, fixtures, dataset

**Arc**:
A planted storyline in the Book that a correct answer must surface, such as a stalled deal or a quiet churner.
_Avoid_: scenario, storyline, signal

**Owner**:
A HubSpot user that CRM records are assigned to.
_Avoid_: seller, rep, HubSpot user

**Effect**:
A realm operation declared to change something in HubSpot; anything not declared an Effect is a read.
_Avoid_: write, mutation, action

**Sensitive Effect**:
An Effect that cannot be taken back or reaches past the business, so a person approves it first whatever the agent is granted.
_Avoid_: guarded write, dangerous write, confirmed write
