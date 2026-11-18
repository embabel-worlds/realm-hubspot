#!/usr/bin/env python3
"""The ladder. See tests/verify.sh for the layers and how to run it.

Every figure is computed from rules, never from known records: L0 reads HubSpot directly, L1 asks
the same of the realm's producers through Me's kg execute door, and the two sets must be equal.
"""
import base64, collections, datetime, json, os, re, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('EMBABEL', 'http://localhost:8042').rstrip('/')
HS = os.environ.get('HUBSPOT_TOKEN') or sys.exit('HUBSPOT_TOKEN is required: L0 reads HubSpot directly')
MCP_TOKEN = os.environ.get('EMBABEL_MCP_TOKEN')
if '--writes' in sys.argv[1:]:
    sys.exit('--writes: not implemented yet')  # TODO: the write leg, against a disposable portal only
TODAY = os.environ.get('TODAY') or datetime.date.today().isoformat()
def days_before(n): return (datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=n)).isoformat()
STALL_CUT, QUIET_CUT = days_before(45), days_before(30)
print(f'TODAY {TODAY}  stalled <= {STALL_CUT}  quiet <= {QUIET_CUT}  against {BASE}')

def me_auth():
    if os.environ.get('EMBABEL_TOKEN'): return 'Bearer ' + os.environ['EMBABEL_TOKEN']
    if os.environ.get('EMBABEL_AUTH'): return 'Basic ' + base64.b64encode(os.environ['EMBABEL_AUTH'].encode()).decode()
    sys.exit('set EMBABEL_AUTH=user:pass or EMBABEL_TOKEN=...')

def http(url, body=None, headers=None, method=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=600) as r: return r.read().decode(), r.headers

fails = []
def check(cond, label, detail=''):
    print(('ok   ' if cond else 'FAIL ') + label + (f'  [{detail}]' if detail and not cond else ''), flush=True)
    if not cond: fails.append(label)

def day(v): return (v or '')[:10]          # a timestamp's date; '' when unset
def stale(v, cut): return not v or day(v) <= cut
def sizes(groups): return {k: len(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}

# ---- L0: HubSpot itself.
hs_calls = 0
def hs_all(obj, props):
    global hs_calls
    out, after = [], None
    while True:
        body = {'limit': 100, 'properties': props, **({'after': after} if after else {})}
        hs_calls += 1
        d = json.loads(http(f'https://api.hubapi.com/crm/v3/objects/{obj}/search', body, {'Authorization': 'Bearer ' + HS})[0])
        out += [r['properties'] for r in d['results']]
        after = (d.get('paging') or {}).get('next', {}).get('after')
        if not after: return out

def group(records, key, keep):
    g = collections.defaultdict(set)
    for r in records:
        if keep(r): g[r.get(key) or None].add(r['hs_object_id'])
    return dict(g)

owners = [o['id'] for o in json.loads(http('https://api.hubapi.com/crm/v3/owners/?limit=100', headers={'Authorization': 'Bearer ' + HS})[0])['results']]
deals = hs_all('deals', ['hs_is_closed', 'closedate', 'notes_last_contacted', 'hubspot_owner_id'])
contacts = hs_all('contacts', ['notes_last_contacted', 'hubspot_owner_id'])
tasks = hs_all('tasks', ['hs_task_status', 'hs_timestamp', 'hubspot_owner_id'])
tickets = hs_all('tickets', ['hs_is_closed', 'hs_primary_company_id'])
open_deals = [d for d in deals if d.get('hs_is_closed') == 'false']
L0 = {
    'stalled': group(open_deals, 'hubspot_owner_id', lambda d: stale(d.get('notes_last_contacted'), STALL_CUT)),
    'slipping': {'all': {d['hs_object_id'] for d in open_deals if d.get('closedate') and day(d['closedate']) < TODAY}},
    'quiet': group(contacts, 'hubspot_owner_id', lambda c: stale(c.get('notes_last_contacted'), QUIET_CUT)),
    'pastDue': group(tasks, 'hubspot_owner_id', lambda t: t.get('hs_task_status') != 'COMPLETED' and t.get('hs_timestamp') and day(t['hs_timestamp']) < TODAY),
    'openTickets': group(tickets, 'hs_primary_company_id', lambda t: t.get('hs_is_closed') == 'false'),
}
print(f'L0: {len(owners)} owners, {len(deals)} deals ({len(open_deals)} open), {len(contacts)} contacts, {len(tasks)} tasks, {len(tickets)} tickets ({hs_calls} search calls)')
check(len(open_deals) > 0 and len(owners) > 0, 'L0: the portal has owners and open deals (an empty portal verifies nothing)')
for name, g in L0.items():
    unkeyed = len(g.pop(None, ()))
    print(f'L0 {name}: {sizes(g)}' + (f'  (+{unkeyed} with no owner/company: no door reaches them by key)' if unkeyed else ''))

# ---- L1: the realm's producers, through Me, by the doors types/hubspot.yml and the skill declare.
def kg(cypher):
    d = json.loads(http(BASE + '/api/v1/admin/kg/execute', {'cypher': cypher}, {'Authorization': me_auth()})[0])
    warn = [w for w in d.get('warnings') or [] if 'PARTIAL' in w or 'cap' in w.lower()]
    check(not d.get('error') and not warn, f'L1: query runs complete ({cypher[:60]}...)', f'{str(d.get("error"))[:200]} {warn[:2]}')
    return d.get('rows') or []
def kg_groups(cypher):
    return {r['k']: set(r['ids']) for r in kg(cypher) if r['ids']}
ids = json.dumps(owners)
OWNER = f"MATCH (o:HubSpotOwner) WHERE o.id IN {ids} "
L1 = {
    'stalled': kg_groups(OWNER + f"MATCH (o)-[:OWNS_DEAL]->(d:HubSpotDeal) WHERE d.hs_is_closed = 'false' AND (coalesce(d.notes_last_contacted, '') = '' OR substring(d.notes_last_contacted, 0, 10) <= '{STALL_CUT}') RETURN o.id AS k, collect(DISTINCT d.hs_object_id) AS ids"),
    'slipping': kg_groups(f"MATCH (b:HubSpotBook {{scope:'all'}})-[:HAS_DEAL]->(d:HubSpotDeal) WHERE d.hs_is_closed = 'false' AND coalesce(d.closedate, '') <> '' AND substring(d.closedate, 0, 10) < '{TODAY}' RETURN 'all' AS k, collect(DISTINCT d.hs_object_id) AS ids"),
    'quiet': kg_groups(OWNER + f"MATCH (o)-[:OWNS_CONTACT]->(c:HubSpotContact) WHERE coalesce(c.notes_last_contacted, '') = '' OR substring(c.notes_last_contacted, 0, 10) <= '{QUIET_CUT}' RETURN o.id AS k, collect(DISTINCT c.hs_object_id) AS ids"),
    'pastDue': kg_groups(OWNER + f"MATCH (o)-[:OWNS_ACTIVITY]->(t:HubSpotTask) WHERE coalesce(t.hs_task_status, '') <> 'COMPLETED' AND coalesce(t.hs_timestamp, '') <> '' AND substring(t.hs_timestamp, 0, 10) < '{TODAY}' RETURN o.id AS k, collect(DISTINCT t.hs_object_id) AS ids"),
    'openTickets': kg_groups("MATCH (b:HubSpotBook {scope:'all'})-[:HAS_COMPANY]->(co:HubSpotCompany)-[:HAS_TICKET]->(t:HubSpotTicket) WHERE t.hs_is_closed = 'false' RETURN co.hs_object_id AS k, collect(DISTINCT t.hs_object_id) AS ids"),
}
L0['slipping'] = {k: v for k, v in L0['slipping'].items() if v}
for name in L0:
    check(L1[name] == L0[name], f'L1: {name} == HubSpot, set for set  {sizes(L1[name])}',
          f'L0 {sizes(L0[name])}; differing keys {sorted(k for k in set(L0[name]) | set(L1[name]) if L0[name].get(k) != L1[name].get(k))[:5]}')

# ---- L2: the write surface, as Me serves it.
def mcp(tool, args):
    if not MCP_TOKEN: sys.exit('set EMBABEL_MCP_TOKEN: L2 reads the served surface over MCP')
    h = {'Authorization': 'Bearer ' + MCP_TOKEN, 'Accept': 'application/json, text/event-stream'}
    def post(body, sid=None):
        raw, hd = http(BASE + '/mcp', body, {**h, **({'Mcp-Session-Id': sid} if sid else {})})
        data = [l[5:].strip() for l in raw.splitlines() if l.startswith('data:')] or [raw]
        return hd.get('Mcp-Session-Id'), (json.loads(data[-1]) if data[-1] else None)
    sid, _ = post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'realm-hubspot-verify', 'version': '1'}}})
    post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, sid)
    _, res = post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}, sid)
    return ''.join(c.get('text', '') for c in (res.get('result') or {}).get('content') or [])

def declared(section):   # apis/apis.yml, the realm's own list; flat enough to read without a YAML parser
    m = re.search(rf'^  {section}:\n((?:    - \S+\n)+)', open(os.path.join(ROOT, 'apis/apis.yml')).read(), re.M)
    return set(re.findall(r'- (\S+)', m.group(1))) if m else set()
reads, writes = declared('operation-ids'), declared('write-operation-ids')
ns = mcp('describe_namespace', {'namespace': 'hubspot'})
served = set(re.findall(r'^  (\w+)\(args', ns, re.M))
check(served == reads | writes, f'L2: served gateway.hubspot has exactly the declared {len(reads)} reads + {len(writes)} writes',
      f'extra {sorted(served - reads - writes)} missing {sorted((reads | writes) - served)}')
spec = json.loads(json.loads(mcp('realm_read', {'realm': 'hubspot', 'path': 'apis/hubspot-crm.json'}))['content'])
ops = {o['operationId']: (verb.upper(), path, o) for path, item in spec['paths'].items() for verb, o in item.items() if isinstance(o, dict) and 'operationId' in o}
check(served <= set(ops), 'L2: every served method is an operation of the installed spec', sorted(served - set(ops)))
check(not any(ops[m][0] == 'DELETE' for m in served if m in ops), 'L2: no served method is a DELETE')
generic = sorted(m for m in writes if m in ops and re.search(r'\{(objectType|fromObjectType)\}', ops[m][1]))
check(not generic, 'L2: no write is generic over object type (no generic create/update)', generic)
check(all(ops[m][0] == 'GET' or m.endswith('Search') for m in reads if m in ops), 'L2: a declared read that is not a GET is a search', sorted(m for m in reads if m in ops and ops[m][0] != 'GET' and not m.endswith('Search')))
noeffect = sorted(m for m in writes if m in ops and 'x-embabel-effect' not in ops[m][2])
check(not noeffect, 'L2: every write declares x-embabel-effect in the served spec', noeffect)
eff = ops.get('dealMoveStage', (None, None, {}))[2].get('x-embabel-effect') or {}
check(bool(eff.get('sensitive')) and eff.get('approval') == 'always', 'L2: dealMoveStage is sensitive with approval always', eff)
upd = ((ops.get('dealUpdate', (None, None, {}))[2].get('requestBody') or {}).get('content') or {}).get('application/json', {}).get('schema', {})
if '$ref' in upd: upd = spec['components']['schemas'][upd['$ref'].rsplit('/', 1)[1]]
props = (upd.get('properties') or {}).get('properties') or {}
check(props.get('additionalProperties') is False and not {'dealstage', 'pipeline'} & set(props.get('properties') or {}),
      'L2: dealUpdate cannot move a stage (closed schema without dealstage or pipeline)')

print('\n' + ('DRIFT DETECTED: ' + '; '.join(fails) if fails else 'ALL CHECKS PASS'))
sys.exit(1 if fails else 0)
