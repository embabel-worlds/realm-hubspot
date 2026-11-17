#!/usr/bin/env python3
"""Load a neutral book into a HubSpot portal, through HubSpot's own CRM API.

A BOOK is a directory of product-neutral CSVs (crm/, support/, billing/) joined by
`account_key`, the customer's domain. This loader reads crm/ and support/cases.csv and knows
only HubSpot. Companies, contacts, deals, tickets, notes, calls, emails, meetings and tasks
are written with their associations and owners.

    HUBSPOT_TOKEN=... ./load_book.py --book .../book --portal 248032427            # plan, no writes
    HUBSPOT_TOKEN=... ./load_book.py --book .../book --portal 248032427 --yes      # load
    HUBSPOT_TOKEN=... ./load_book.py --book .../book --portal 248032427 --remove --yes
    ./load_book.py --book .../book --self-check                                    # offline

It follows the realm spec's rules for seeds:
- It refuses to run blind. The token's portal must equal --portal, and nothing is written
  without --yes. Without --yes it only reads and prints what it would do.
- It is idempotent. Every record carries `book_row_id`, a unique property this loader
  creates, and is upserted on it, so a second run updates in place.
- Its records are recognizable: --remove deletes everything carrying `book_row_id`.

HubSpot cannot backdate a contact's or company's creation, so history is carried by time
instead: every date moves forward by (today - the book's as_of), so the book's "now" lands
on the day of the load. The "last contacted" rollups follow the engagement timestamps.
Before loading it deletes the sample records HubSpot plants in a new portal.
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

API = "https://api.hubapi.com"
MARK = "book_row_id"
ENGAGEMENTS = ["notes", "calls", "emails", "meetings", "tasks"]
OBJECTS = ["companies", "contacts", "deals", "tickets"] + ENGAGEMENTS
GROUP = {"companies": "companyinformation", "contacts": "contactinformation",
         "deals": "dealinformation", "tickets": "ticketinformation"}  # engagements: "engagement"
# Book words HubSpot spells differently; everything else is matched against the portal's own options.
LIFECYCLE = {"prospect": "lead"}
CATEGORY = {"bug": "product issue", "enhancement": "feature request",
            "question": "general inquiry", "documentation": "general inquiry"}


def rows(book, name):
    with open(book / name, newline="") as f:
        return list(csv.DictReader(f))


def norm(s):
    s = (s or "").lower().replace("&", " and ").replace("/", " and ")
    return " ".join(s.replace("_", " ").replace("-", " ").replace(",", " ").split())


def match(options, text):
    """The one option whose label or value reads like `text`: exact, then prefix, then whole words inside.
    A looser test that fits several options is ambiguous and gives None, rather than a guess."""
    t = norm(text)
    if not t:
        return None
    for test in (lambda l: l == t, lambda l: l.startswith(t), lambda l: f" {t} " in f" {l} "):
        hits = {value for label, value in options if test(norm(label)) or test(norm(value))}
        if hits:
            return hits.pop() if len(hits) == 1 else None
    return None


def ms(when, shift):
    """Epoch millis of a book date or datetime moved by `shift`. Book clocks are read as UTC; a bare
    date is placed at 12:00 UTC so it stays the same calendar day in the portal's US time zone."""
    t = dt.datetime.fromisoformat(when + " 12:00:00" if len(when) == 10 else when)
    t = t.astimezone(dt.timezone.utc) if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return str(int((t + shift).timestamp() * 1000))


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
NAMES = sorted({n for f in MONTHS for n in (f, f[:3])} | {"Sept"}, key=len, reverse=True)
M = "|".join(NAMES)
DATES = re.compile(r"\b(?P<iso>(\d{4})-(\d\d)-(\d\d))\b"
                   r"|\b(?P<name>" + M + r")(?P<dot>\.?) (?P<day>\d{1,2})(?P<sfx>st|nd|rd|th)?\b"
                   r"|\b(?P<day2>\d{1,2})(?P<sfx2>st|nd|rd|th)? (?P<name2>" + M + r")(?P<dot2>\.?)(?=\W|$)")
TEXT = {"hs_email_subject", "hs_email_text", "hs_call_title", "hs_call_body", "hs_note_body", "hs_task_subject",
        "hs_task_body", "hs_meeting_title", "description", "subject", "content"}


def shift_text(text, shift, year):
    """Move dates written inside a text by `shift`, as the structured dates move, keeping each one's spelling style.
    A month-day without a year is read in the book's year and gains its year once the shift moves it past that year.
    Weekday words stay right because the shift is whole weeks (week_shift); day-only references ("the 11th") are not
    moved, so the book must not use them."""
    def move(m):
        if m["iso"]:
            return (dt.date.fromisoformat(m["iso"]) + shift).isoformat()
        name, dot, day, suffix = (m["name"], m["dot"], m["day"], m["sfx"]) if m["name"] else \
            (m["name2"], m["dot2"], m["day2"], m["sfx2"])
        full = next(f for f in MONTHS if f.startswith(name[:3]))
        try:
            d = dt.date(year, MONTHS.index(full) + 1, int(day)) + shift
        except ValueError:
            return m.group(0)
        month = (MONTHS[d.month - 1] if name == full else MONTHS[d.month - 1][:3]) + dot
        sfx = "" if not suffix else "th" if 10 < d.day % 100 < 14 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
        when = f"{month} {d.day}{sfx}" if m["name"] else f"{d.day}{sfx} {month}"
        return when if d.year == year else f"{when}, {d.year}" if m["name"] else f"{when} {d.year}"
    return DATES.sub(move, text) if isinstance(text, str) else text


def week_shift(today, as_of):
    """The whole number of weeks nearest to today - as_of, so every weekday in the book stays its weekday.
    The book lands up to 3 days from today; its signals keep more than that from every cut-off (MARGIN 4)."""
    return dt.timedelta(weeks=round((today - as_of).days / 7))


def plan(book, shift, owner, option, stage, ticket_stage):
    """Every record and association the book becomes, keyed by book_row_id. Pure: the portal
    lookups come in as functions, so --self-check runs it offline."""
    rec = {o: {} for o in OBJECTS}
    year = book_as_of(book).year
    links, gaps = [], Counter()
    accounts, contacts = rows(book, "crm/accounts.csv"), rows(book, "crm/contacts.csv")
    deals, cases = rows(book, "crm/opportunities.csv"), rows(book, "support/cases.csv")
    company = {a["account_key"]: a["source_id"] for a in accounts}
    person = {c["email"]: c["source_id"] for c in contacts}
    deal = {d["source_id"]: d["source_id"] for d in deals}

    def put(obj, sid, props, owner_name=None):
        if owner_name is not None:
            props["hubspot_owner_id"] = owner(owner_name)
            gaps[f"owner not in portal: {owner_name}"] += props["hubspot_owner_id"] is None
        rec[obj][sid] = {k: shift_text(v, shift, year) if k in TEXT else v
                         for k, v in props.items() if v not in (None, "")}

    def link(obj, sid, account_key=None, email=None, opportunity=None):
        for to, key, ids in (("companies", account_key, company), ("contacts", email, person),
                             ("deals", opportunity, deal)):
            if key:
                if key in ids:
                    links.append((obj, sid, to, ids[key]))
                else:
                    gaps[f"{obj} → {to}: {key} not in book"] += 1

    def opt(obj, prop, text):
        value = option(obj, prop, text)
        gaps[f"{obj}.{prop} has no option for '{text}'"] += bool(text) and value is None
        return value

    for a in accounts:
        rel = a["relationship"]
        put("companies", a["source_id"], {
            "name": a["name"], "domain": a["account_key"], "website": a["website"], "phone": a["phone"],
            "industry": opt("companies", "industry", a["industry"]), "book_industry": a["industry"], "city": a["city"], "state": a["region"],
            "country": a["country"], "lifecyclestage": opt("companies", "lifecyclestage", LIFECYCLE.get(rel, rel)),
            "annualrevenue": a["annual_revenue"], "numberofemployees": a["employees"], "description": a["note"]},
            a["owner"])
    for c in contacts:
        put("contacts", c["source_id"], {
            "email": c["email"], "firstname": c["first_name"], "lastname": c["last_name"],
            "jobtitle": c["job_title"], "phone": c["phone"], "city": c["city"], "state": c["region"],
            "country": c["country"]}, c["owner"])
        link("contacts", c["source_id"], c["account_key"])
        if c["note"]:  # HubSpot contacts have no description; the book's note becomes a note on them
            sid = "contact:" + c["source_id"]
            put("notes", sid, {"hs_note_body": c["note"],
                               "hs_timestamp": ms(c["note_on"], shift)}, c["owner"])
            link("notes", sid, c["account_key"], c["email"])
    for d in deals:
        pipeline, dealstage = stage(d["pipeline"], d["stage"])
        gaps[f"deals.dealstage has no stage '{d['stage']}' in '{d['pipeline']}'"] += dealstage is None
        put("deals", d["source_id"], {
            "dealname": d["title"], "amount": d["amount"], "pipeline": pipeline, "dealstage": dealstage,
            "closedate": ms(d["close_on"], shift), "createdate": ms(d["created_on"], shift),
            "dealtype": opt("deals", "dealtype", d["kind"]), "hs_priority": opt("deals", "hs_priority", d["priority"]),
            "description": d["note"]}, d["owner"])
        link("deals", d["source_id"], d["account_key"], d["contact_email"])
    for t in cases:
        put("tickets", t["source_id"], {
            "subject": t["subject"], "content": t["body"], **ticket_stage(t["status"]),
            "hs_ticket_priority": opt("tickets", "hs_ticket_priority", "" if t["priority"] == "none" else t["priority"]),
            "hs_ticket_category": opt("tickets", "hs_ticket_category", CATEGORY.get(t["kind"], t["kind"])),
            "createdate": ms(t["opened_on"], shift),
            "closed_date": t["closed_on"] and ms(t["closed_on"], shift)})
        link("tickets", t["source_id"], t["account_key"], t["requester_email"])
    for e in rows(book, "crm/emails.csv"):
        put("emails", e["source_id"], {
            "hs_timestamp": ms(e["sent_at"], shift), "hs_email_subject": e["subject"], "hs_email_text": e["body"],
            "hs_email_direction": "EMAIL" if e["direction"] == "outbound" else "INCOMING_EMAIL",
            "hs_email_status": "SENT"}, e["owner"])
        link("emails", e["source_id"], e["account_key"], e["contact_email"], e["opportunity_id"])
    for c in rows(book, "crm/calls.csv"):
        put("calls", c["source_id"], {
            "hs_timestamp": ms(c["called_at"], shift), "hs_call_title": c["title"], "hs_call_body": c["body"],
            "hs_call_direction": c["direction"].upper(), "hs_call_duration": str(int(c["minutes"] or 0) * 60000),
            "hs_call_status": "COMPLETED", "hs_call_disposition": opt("calls", "hs_call_disposition", c["outcome"])},
            c["owner"])
        link("calls", c["source_id"], c["account_key"], c["contact_email"], c["opportunity_id"])
    as_of = book_as_of(book)
    for m in rows(book, "crm/meetings.csv"):
        start = dt.datetime.fromisoformat(m["starts_at"])
        end = (start + dt.timedelta(minutes=int(m["minutes"]))).isoformat(" ")
        put("meetings", m["source_id"], {
            "hs_timestamp": ms(m["starts_at"], shift), "hs_meeting_title": m["title"],
            "hs_meeting_start_time": ms(m["starts_at"], shift), "hs_meeting_end_time": ms(end, shift),
            "hs_meeting_outcome": "COMPLETED" if start.date() <= as_of else "SCHEDULED"}, m["organizer"])
        link("meetings", m["source_id"], m["account_key"], m["attendee_email"])
    for n in rows(book, "crm/notes.csv"):
        put("notes", n["source_id"], {"hs_note_body": n["body"], "hs_timestamp": ms(n["written_on"], shift)},
            n["author"])
        link("notes", n["source_id"], n["account_key"], opportunity=n["opportunity_id"])
    for f in rows(book, "crm/followups.csv"):
        put("tasks", f["source_id"], {
            "hs_task_subject": f["summary"], "hs_task_body": f["note"], "hs_timestamp": ms(f["due_on"], shift),
            "hs_task_status": "COMPLETED" if f["status"] == "done" else "NOT_STARTED", "hs_task_type": "TODO"},
            f["owner"])
        link("tasks", f["source_id"], f["account_key"])
    for o in OBJECTS:
        for sid, props in rec[o].items():
            props[MARK] = sid
    return rec, links, +gaps


def last_contacted(rec, links, target):
    """Per `target` sid (contacts, companies or deals): the newest linked call, outbound email or meeting timestamp
    HubSpot rolls up. Measured on the Test Portal 2026-11-17: inbound emails do not count toward notes_last_contacted."""
    out = {}
    for obj, sid, to, cid in links:
        if to == target and obj in ("calls", "emails", "meetings") and rec[obj][sid].get("hs_email_direction") != "INCOMING_EMAIL":
            out[cid] = max(out.get(cid, "0"), rec[obj][sid]["hs_timestamp"], key=int)
    return out


def book_as_of(book):
    return dt.date.fromisoformat(json.loads((book / "book.json").read_text())["as_of"])


class HubSpot:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None, missing_ok=False, retry_5xx=True):
        for attempt in range(6):
            req = urllib.request.Request(API + path, method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + self.token,
                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req) as r:
                    raw = r.read()
                    out = json.loads(raw) if raw else {}
                    # Batch calls report per-item failures inside a 2xx (207, or 200 with numErrors).
                    if isinstance(out, dict) and (out.get("errors") or out.get("numErrors")):
                        raise SystemExit(f"HubSpot partly refused {method} {path}: {json.dumps(out.get('errors'))[:600]}")
                    return out
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")
                if e.code == 404 and missing_ok:
                    return None
                if e.code == 429 or (e.code >= 500 and retry_5xx):
                    after = e.headers.get("Retry-After") or ""
                    time.sleep(int(after) if after.isdigit() else 2 ** attempt)
                    continue
                raise SystemExit(f"HubSpot refused {method} {path}: {e.code} {detail[:600]}")
        raise SystemExit(f"HubSpot kept throttling {method} {path}")

    def search(self, obj, filters, props=()):
        found, after = [], None
        while True:
            page = self.call("POST", f"/crm/v3/objects/{obj}/search", {
                "filterGroups": [{"filters": filters}], "properties": list(props), "limit": 100,
                **({"after": after} if after else {})})
            found += page["results"]
            after = page.get("paging", {}).get("next", {}).get("after")
            if not after:
                return found

    def archive(self, obj, ids):
        for i in range(0, len(ids), 100):
            self.call("POST", f"/crm/v3/objects/{obj}/batch/archive", {"inputs": [{"id": x} for x in ids[i:i + 100]]})


class Portal:
    """The portal's own vocabulary: owners, pipelines and enumeration options, read once."""

    def __init__(self, hs):
        self.hs, self.options = hs, {}
        owners = hs.call("GET", "/crm/v3/owners/?limit=500")["results"]
        self.owners = {f"{o.get('firstName', '')} {o.get('lastName', '')}".strip(): o["id"] for o in owners}
        self.dispositions = [(d["label"], d["id"]) for d in hs.call("GET", "/calling/v1/dispositions")]
        self.read_pipelines()

    def read_pipelines(self):
        self.deal_pipelines = self.hs.call("GET", "/crm/v3/pipelines/deals")["results"]
        tickets = sorted(self.hs.call("GET", "/crm/v3/pipelines/tickets")["results"], key=lambda p: p["displayOrder"])
        self.ticket_pipeline = tickets[0]

    def option(self, obj, prop, text):
        if (obj, prop) == ("calls", "hs_call_disposition"):
            return match(self.dispositions, text)
        if (obj, prop) not in self.options:
            p = self.hs.call("GET", f"/crm/v3/properties/{obj}/{prop}")
            self.options[obj, prop] = [(o["label"], o["value"]) for o in p.get("options", [])]
        return match(self.options[obj, prop], text)

    def stage(self, pipeline, label):
        for p in self.deal_pipelines:
            if norm(p["label"]) == norm(pipeline):
                return p["id"], match([(s["label"], s["id"]) for s in p["stages"]], label)
        return f"(new pipeline {pipeline})", f"(new stage {label})"

    def ticket_stage(self, status):
        """The pipeline's first stage of the matching state: open → first OPEN, anything else → first CLOSED."""
        state = "OPEN" if status == "open" else "CLOSED"
        stages = sorted(self.ticket_pipeline["stages"], key=lambda s: s["displayOrder"])
        sid = next(s["id"] for s in stages if s["metadata"].get("ticketState") == state)
        return {"hs_pipeline": self.ticket_pipeline["id"], "hs_pipeline_stage": sid}

    def missing_pipelines(self, book):
        have = {norm(p["label"]) for p in self.deal_pipelines}
        return sorted({d["pipeline"] for d in rows(book, "crm/opportunities.csv") if norm(d["pipeline"]) not in have})

    def create_pipeline(self, label):
        """A book pipeline the portal lacks, created with the stages of the portal's default pipeline."""
        base = next(p for p in self.deal_pipelines if p["id"] == "default")
        self.hs.call("POST", "/crm/v3/pipelines/deals", retry_5xx=False, body={
            "label": label, "displayOrder": len(self.deal_pipelines),
            "stages": [{"label": s["label"], "displayOrder": s["displayOrder"],
                        "metadata": {"probability": s["metadata"]["probability"]}} for s in base["stages"]]})


def ensure_property(hs, obj, name, label, description, unique=False):
    if hs.call("GET", f"/crm/v3/properties/{obj}/{name}", missing_ok=True):
        return False
    gone = hs.call("GET", f"/crm/v3/properties/{obj}/{name}?archived=true", missing_ok=True)
    if gone and gone.get("hasUniqueValue") != unique:  # HubSpot revives an archived name but never changes its uniqueness
        sys.exit(f"{obj} has an archived property {name} with hasUniqueValue={gone.get('hasUniqueValue')}; "
                 f"HubSpot will not recreate it as {unique}. Use another name or restore it by hand.")
    hs.call("POST", f"/crm/v3/properties/{obj}", {
        "name": name, "label": label, "type": "string", "fieldType": "text",
        "groupName": GROUP.get(obj, "engagement"), "hasUniqueValue": unique, "description": description})
    return True


def upsert(hs, obj, records):
    """Upsert on book_row_id; returns {sid: HubSpot id}."""
    ids, items = {}, list(records.items())
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        hs.call("POST", f"/crm/v3/objects/{obj}/batch/upsert", {
            "inputs": [{"idProperty": MARK, "id": sid, "properties": props} for sid, props in chunk]})
        got = hs.call("POST", f"/crm/v3/objects/{obj}/batch/read", {
            "idProperty": MARK, "properties": [MARK], "inputs": [{"id": sid} for sid, _ in chunk]})
        ids.update({r["properties"][MARK]: r["id"] for r in got["results"]})
    return ids


def associate(hs, links, ids):
    pairs = {}
    for obj, sid, to, tid in links:
        pairs.setdefault((obj, to), []).append({"from": {"id": ids[obj][sid]}, "to": {"id": ids[to][tid]}})
    for (obj, to), inputs in pairs.items():
        for i in range(0, len(inputs), 100):
            hs.call("POST", f"/crm/v4/associations/{obj}/{to}/batch/associate/default", {"inputs": inputs[i:i + 100]})
    return len(links)


def poll(hs, obj, ids, expected, limit=180):
    """Wait until every `obj` record's notes_last_contacted equals the newest call/email/meeting; returns misses."""
    deadline, items = time.time() + limit, list(expected)
    back = {v: k for k, v in ids[obj].items()}
    while True:
        got = {}
        for i in range(0, len(items), 100):
            r = hs.call("POST", f"/crm/v3/objects/{obj}/batch/read", {
                "properties": ["notes_last_contacted"], "inputs": [{"id": ids[obj][s]} for s in items[i:i + 100]]})
            got.update({back[x["id"]]: x["properties"].get("notes_last_contacted") for x in r["results"]})
        misses = {s: (got.get(s), want) for s, want in expected.items()
                  if not got.get(s) or abs(int(dt.datetime.fromisoformat(got[s].replace("Z", "+00:00")).timestamp() * 1000) - int(want)) > 1000}
        if not misses or time.time() > deadline:
            return misses
        time.sleep(15)


def with_manifest(seeded, manifest, portal_id):
    """Add a previous load's ids (search is not read-your-writes), but only a manifest of this very portal."""
    if not manifest.exists():
        return seeded
    m = json.loads(manifest.read_text())
    if m.get("portal") != portal_id:
        sys.exit(f"{manifest} records portal {m.get('portal')}, not {portal_id}; refusing to delete by its ids")
    return {o: sorted(set(seeded.get(o, [])) | set(m["ids"].get(o, {}).values())) for o in set(seeded) | set(m["ids"])}


def summary(rec, links, gaps):
    print("records: " + ", ".join(f"{len(rec[o])} {o}" for o in OBJECTS) + f"; associations: {len(links)}")
    for g, n in sorted(gaps.items()):
        print(f"  gap: {g} ({n}×)")


def self_check(book):
    shift = dt.timedelta(days=143)
    rec, links, gaps = plan(book, shift, owner=lambda n: "1", option=lambda o, p, t: t,
                            stage=lambda p, s: (p, s), ticket_stage=lambda s: {"hs_pipeline_stage": s})
    assert len(rec["emails"]) == len(rows(book, "crm/emails.csv")) and len(rec["tickets"]) == len(rows(book, "support/cases.csv"))
    first = rows(book, "crm/emails.csv")[0]
    want = dt.datetime.fromisoformat(first["sent_at"]).replace(tzinfo=dt.timezone.utc) + shift
    assert rec["emails"][first["source_id"]]["hs_timestamp"] == str(int(want.timestamp() * 1000))
    assert dt.datetime.fromtimestamp(int(ms("2026-06-27", shift)) / 1000, dt.timezone.utc).date() == dt.date(2026, 11, 17)
    assert ms("2026-06-27T10:00:00", shift) == ms("2026-06-27 10:00:00", shift) == ms("2026-06-27T12:00:00+02:00", shift)
    assert all(sid in rec[o] and tid in rec[t] for o, sid, t, tid in links)
    assert shift_text("Tuesday 16 June or the 2nd July; May we?", dt.timedelta(weeks=20), 2026) == \
        "Tuesday 3 November or the 19th November; May we?"
    assert shift_text("by October 1 or 1st Oct.", dt.timedelta(weeks=20), 2026) == "by February 18, 2027 or 18th Feb. 2027"
    assert week_shift(dt.date(2026, 11, 17), dt.date(2026, 6, 27)) == dt.timedelta(days=140)
    assert week_shift(dt.date(2026, 11, 18), dt.date(2026, 6, 27)) == dt.timedelta(days=147)
    assert shift_text("presentation on June 30, call Mar. 3rd, due 2026-06-24; May we?", shift, 2026) == \
        "presentation on November 20, call Jul. 24th, due 2026-11-14; May we?"
    assert all(p[MARK] == sid for o in OBJECTS for sid, p in rec[o].items())
    assert not gaps, gaps
    assert match([("Left voicemail", "v"), ("No answer", "n")], "voicemail") == "v"
    assert match([("No answer", "n")], "no_answer") == "n" and match([("Computer Software", "CS")], "Software") == "CS"
    assert match([("Biotechnology", "B"), ("Information Technology", "IT")], "Technology") == "IT"
    assert match([("Import and Export", "IE")], "Import/Export") == "IE"
    assert match([("Civil Engineering", "C"), ("Mechanical Engineering", "M")], "Engineering") is None
    assert last_contacted(rec, links, "contacts") and last_contacted(rec, links, "deals"), "nothing to roll up"
    _, _, bad = plan(book, shift, owner=lambda n: "1", option=lambda o, p, t: t,
                     stage=lambda p, s: (p, None), ticket_stage=lambda s: {})
    assert any(g.startswith("deals.dealstage") for g in bad), "an unmatched deal stage went unreported"

    stale = Path(tempfile.gettempdir()) / f"load_book-selfcheck-{os.getpid()}.json"
    stale.write_text(json.dumps({"portal": 111, "ids": {"contacts": {"101": "123"}}}))
    try:
        with_manifest({}, stale, 222)
        raise AssertionError("another portal's manifest was trusted for deletion")
    except SystemExit:
        pass
    assert with_manifest({"contacts": ["9"]}, stale, 111) == {"contacts": ["123", "9"]}
    stale.unlink()

    class PartlyRefused(io.BytesIO):  # a batch answer: HTTP 2xx carrying per-item errors
        pass
    real, urllib.request.urlopen = urllib.request.urlopen, lambda req: PartlyRefused(b'{"numErrors": 1, "errors": [{"message": "no"}]}')
    try:
        HubSpot("offline").call("POST", "/crm/v3/objects/tickets/batch/upsert", {})
        raise AssertionError("a 2xx with per-item errors passed as success")
    except SystemExit:
        pass
    finally:
        urllib.request.urlopen = real
    summary(rec, links, gaps)
    print("self-check ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--book", required=True, type=Path, help="directory holding book.json, crm/ and support/")
    ap.add_argument("--portal", type=int, help="the portal id the token must belong to")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today(), help="the book's as_of lands on the same weekday nearest this day")
    ap.add_argument("--manifest", type=Path, default=Path(__file__).with_name("seeded.json"))
    ap.add_argument("--remove", action="store_true", help="delete every record carrying book_row_id")
    ap.add_argument("--yes", action="store_true", help="write; without it the loader only reads and plans")
    ap.add_argument("--self-check", action="store_true", help="plan offline and assert, touching no portal")
    args = ap.parse_args()
    if args.self_check:
        return self_check(args.book)
    token = os.environ.get("HUBSPOT_TOKEN") or sys.exit("HUBSPOT_TOKEN is not set")
    args.portal or sys.exit("--portal is required: the id of the portal you have decided is disposable")
    hs = HubSpot(token)
    portal_id = hs.call("GET", "/account-info/v3/details")["portalId"]
    if portal_id != args.portal:
        sys.exit(f"the token belongs to portal {portal_id}, not {args.portal}; refusing")
    seeded = {o: [r["id"] for r in hs.search(o, [{"propertyName": MARK, "operator": "HAS_PROPERTY"}])]
              for o in OBJECTS if hs.call("GET", f"/crm/v3/properties/{o}/{MARK}", missing_ok=True)}
    if args.remove:
        seeded = with_manifest(seeded, args.manifest, portal_id)
        print("would remove" if not args.yes else "removing", ", ".join(f"{len(v)} {o}" for o, v in seeded.items()) or "nothing")
        if args.yes:
            for o, v in seeded.items():
                hs.archive(o, v)
        return print("left in place: the book_row_id and book_industry properties, any pipeline the load created,\n"
                     "  and the owners; HubSpot's deleted sample records stay in its recycle bin.")
    samples = {o: [r["id"] for r in hs.search(o, [{"propertyName": "hs_object_source_id", "operator": "EQ", "value": "sample-contact"}])]
               for o in OBJECTS}
    shift = week_shift(args.today, book_as_of(args.book))
    ctx = Portal(hs)
    new_pipelines = ctx.missing_pipelines(args.book)
    print(f"portal {portal_id}; book as_of {book_as_of(args.book)} lands on {book_as_of(args.book) + shift} "
          f"(shift {shift.days} days, whole weeks nearest to {args.today})")
    print("HubSpot sample records to delete: " + (", ".join(f"{len(v)} {o}" for o, v in samples.items() if v) or "none"))
    print("already seeded: " + (", ".join(f"{len(v)} {o}" for o, v in seeded.items() if v) or "none"))
    print(f"{MARK} property to create on: " + (", ".join(o for o in OBJECTS if o not in seeded) or "none")
          + "; book_industry on companies, if missing")
    print("deal pipelines to create (default stages): " + (", ".join(new_pipelines) or "none"))
    if not args.yes:
        rec, links, gaps = plan(args.book, shift, ctx.owners.get, ctx.option, ctx.stage, ctx.ticket_stage)
        summary(rec, links, gaps)
        print("not carried: contact/company created-on (HubSpot stamps its own), deal probability (from the stage),\n"
              "  task completed-on, meeting booked-on, ticket labels/engineers/milestone/reply count.")
        return print("plan only; re-run with --yes to write")
    # Configuration first: if the key lacks a schema scope, nothing has been deleted yet.
    for o in OBJECTS:
        ensure_property(hs, o, MARK, "Book row id", "Row id in the sample business book this record was loaded from.", unique=True)
    # HubSpot's industry list is fixed and misses much of the book's wording, so the book's own word rides along.
    ensure_property(hs, "companies", "book_industry", "Book industry", "The industry as the sample business book words it.")
    for p in new_pipelines:
        ctx.create_pipeline(p)
    ctx.read_pipelines()
    rec, links, gaps = plan(args.book, shift, ctx.owners.get, ctx.option, ctx.stage, ctx.ticket_stage)
    summary(rec, links, gaps)
    unloadable = [g for g in gaps if g.startswith(("owner", "deals.dealstage"))]
    unloadable and sys.exit(f"refusing to load with unmapped owners or stages: {unloadable}")
    for o, v in samples.items():
        hs.archive(o, v)
    ids = {o: upsert(hs, o, rec[o]) for o in OBJECTS}
    missing = {o: len(rec[o]) - len(ids[o]) for o in OBJECTS if len(rec[o]) != len(ids[o])}
    missing and sys.exit(f"upsert left records unaccounted for: {missing}")
    n = associate(hs, links, ids)
    expected = {o: last_contacted(rec, links, o) for o in ("contacts", "companies", "deals")}
    args.manifest.write_text(json.dumps({
        "portal": portal_id, "today": args.today.isoformat(), "lands_on": (book_as_of(args.book) + shift).isoformat(), "as_of": book_as_of(args.book).isoformat(),
        "shift_days": shift.days, "ids": ids, "notes_last_contacted": expected}, indent=1, sort_keys=True))
    print(f"loaded; {n} associations; manifest {args.manifest}; waiting for HubSpot's rollups…")
    for o, want in expected.items():
        misses = poll(hs, o, ids, want)
        if misses:
            sys.exit(f"{o} notes_last_contacted differs on {len(misses)} of {len(want)}, e.g. "
                     + "; ".join(f"{s}: got {g} want {w}" for s, (g, w) in list(misses.items())[:5]))
        print(f"{o} notes_last_contacted settled on all {len(want)}")


if __name__ == "__main__":
    main()
