#!/bin/sh
# Ground-truth verification for realm-hubspot, against a live portal and a running Embabel Me.
# Runs the ladder in tests/verify.py; every level reconciles exactly with L0 and names no record:
#   L0 HubSpot itself  — from the REST API directly, for a given TODAY: open deals stalled (no
#                        contact in 45+ days) per owner, open deals slipping (close date passed),
#                        contacts gone quiet 30 days per owner, open tasks past due per owner,
#                        open tickets per company
#   L1 the producers   — the same sets through Me's kg execute door, by the doors the types and
#                        the hubspot-crm skill declare (owner by id, the book)
#   L2 the write surface — gateway.hubspot as Me serves it (describe_namespace) equals apis/apis.yml;
#                        no DELETE, no generic create/update, every write declares its effect, and
#                        dealMoveStage is sensitive with approval always (from the spec Me serves)
# Read-only: nothing is written to HubSpot or Me. --writes is reserved for a write leg (not yet).
# Exits nonzero on any drift.
#
# Environment:
#   HUBSPOT_TOKEN      a token for the portal the realm is connected to (read scopes suffice)
#   EMBABEL            Me's base URL (default http://localhost:8042)
#   EMBABEL_AUTH       user:pass for the kg door, or EMBABEL_TOKEN=<API key> instead
#   EMBABEL_MCP_TOKEN  bearer token for Me's /mcp door (L2)
#   TODAY              YYYY-MM-DD the signals are judged at (default today)
#
#   HUBSPOT_TOKEN=... EMBABEL=http://localhost:8042 EMBABEL_AUTH=user:pass EMBABEL_MCP_TOKEN=... sh tests/verify.sh
# Secrets travel in environment and request headers only, never on a command line.
set -e
cd "$(dirname "$0")/.."
exec python3 tests/verify.py "$@"
