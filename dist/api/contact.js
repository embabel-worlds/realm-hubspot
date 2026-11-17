"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.HubSpotContact = void 0;
const runtime_types_1 = require("@embabel/runtime-types");
const DAY_MS = 86_400_000;
// ─── The type ───────────────────────────────────────────────────────────────
/**
 * A HubSpot CRM contact materialized on demand by virtual cypher (see
 * `types/hubspot.yml` + `producers/hubspot.yml`) — keyed by `email`. The read
 * brings it in transiently and rolls back; a verb acts on the live source.
 *
 *  - **pure** verbs compute over the node's own scalar fields (name, contact
 *    recency, follow-up need), no I/O.
 *  - **effectful** verbs log a note or a task through `this.gateway.hubspot.*`:
 *    they resolve the numeric id once, create the object, and associate it.
 */
class HubSpotContact extends runtime_types_1.Entity {
    // `id` (the identity key) is inherited from Entity and equals the email.
    email;
    firstname;
    lastname;
    jobtitle;
    company;
    hubspot_owner_id;
    notes_last_contacted;
    hs_last_sales_activity_timestamp;
    get api() {
        return this.gateway;
    }
    // ── pure verbs: compute over node state, no I/O ──
    /** Best display name from first/last, falling back to the email. */
    fullName() {
        const name = [this.firstname, this.lastname].filter(Boolean).join(" ").trim();
        return name || this.email;
    }
    /** Days since the contact was last contacted (any logged channel), or null if never. */
    daysSinceContacted(now = Date.now()) {
        if (!this.notes_last_contacted)
            return null;
        return Math.floor((now - Date.parse(this.notes_last_contacted)) / DAY_MS);
    }
    /** Never contacted, or not contacted within `days` (default 30). */
    isStale(days = 30, now = Date.now()) {
        const since = this.daysSinceContacted(now);
        return since === null || since > days;
    }
    /** A contact this owner should follow up: stale past `days` (default 14). */
    needsFollowUp(days = 14, now = Date.now()) {
        return this.isStale(days, now);
    }
    // ── effectful verbs: log notes and tasks through gateway.hubspot (the source) ──
    /** Resolve the numeric HubSpot object id from this contact's email. */
    async resolveId() {
        const found = await this.api.hubspot.objectsGet({ objectType: "contacts", objectId: this.email, idProperty: "email" });
        if (!found?.id)
            throw new Error(`cannot resolve HubSpot contact id for email: ${this.email}`);
        return found.id;
    }
    /** Log a note against the contact, associated to it. */
    async logNote(body, now = Date.now()) {
        const contactId = await this.resolveId();
        const note = await this.api.hubspot.notesCreate({
            properties: { hs_note_body: body, hs_timestamp: new Date(now).toISOString() },
        });
        if (note?.id) {
            await this.api.hubspot.associationsCreateDefault({
                fromObjectType: "notes", fromObjectId: note.id, toObjectType: "contacts", toObjectId: contactId,
            });
        }
        return note;
    }
    /** Create a follow-up task on the contact, due at `dueIso` (default: now). */
    async createFollowUpTask(title, dueIso) {
        const contactId = await this.resolveId();
        const task = await this.api.hubspot.tasksCreate({
            properties: {
                hs_task_subject: title,
                hs_timestamp: dueIso ?? new Date().toISOString(),
                hs_task_status: "NOT_STARTED",
                hs_task_type: "TODO",
            },
        });
        if (task?.id) {
            await this.api.hubspot.associationsCreateDefault({
                fromObjectType: "tasks", fromObjectId: task.id, toObjectType: "contacts", toObjectId: contactId,
            });
        }
        return task;
    }
}
exports.HubSpotContact = HubSpotContact;
