#!/usr/bin/env python3
"""Dump the REST OpenAPI spec and WebSocket event schemas to JSON files.

These artifacts are generated programmatically from the running codebase so
they stay in sync with the actual API and WS contracts. They're intended for
consumption by external integrations (e.g., Home Assistant) that need a stable
reference without reading our source.

Usage:
    PYTHONPATH=. uv run python3 scripts/build/dump_api_specs.py [output_dir]

Output (default: references/ha/):
    openapi.json        — Full OpenAPI 3.x spec for all REST endpoints
    ws_events.json      — JSON Schema for each WebSocket event type
"""

import json
import sys
from pathlib import Path


def dump_openapi(output_dir: Path) -> None:
    from app.main import app

    schema = app.openapi()
    out = output_dir / "openapi.json"
    out.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"  openapi.json: {len(schema['paths'])} paths, "
          f"{len(schema.get('components', {}).get('schemas', {}))} schemas")


def dump_ws_events(output_dir: Path) -> None:
    from app.events import _PAYLOAD_ADAPTERS

    events: dict = {}
    for event_type, adapter in _PAYLOAD_ADAPTERS.items():
        schema = adapter.json_schema()
        events[event_type] = {
            "description": _event_descriptions().get(event_type, ""),
            "payload_schema": schema,
        }

    wrapper = {
        "$comment": (
            "Auto-generated from app/events.py. "
            "Each WebSocket message is a JSON object: {\"type\": \"<event_type>\", \"data\": <payload>}. "
            "The client also sends \"ping\" as plain text; the server replies {\"type\": \"pong\"}."
        ),
        "events": events,
    }

    out = output_dir / "ws_events.json"
    out.write_text(json.dumps(wrapper, indent=2) + "\n")
    print(f"  ws_events.json: {len(events)} event types")


def _event_descriptions() -> dict[str, str]:
    return {
        "health": "Radio connection status. Sent on WS connect and on every state change.",
        "message": "New or incoming message (DM or channel). Includes outgoing messages sent by this radio.",
        "contact": "Contact created or updated (from advertisements, radio sync, or API).",
        "contact_resolved": "A prefix-only placeholder contact was resolved to a full public key.",
        "channel": "Channel created or updated.",
        "contact_deleted": "A contact was removed from the database.",
        "channel_deleted": "A channel was removed from the database.",
        "raw_packet": "Every incoming RF packet (pre-decryption). Use observation_id as the dedup key, not id.",
        "message_acked": "An existing message received an ACK or echo/repeat update.",
        "error": "Toast-level error notification (e.g., radio setup failure, missing private key).",
        "success": "Toast-level success notification (e.g., historical decrypt complete).",
    }


def main() -> None:
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("references/ha")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Dumping API specs to {output_dir}/")
    dump_openapi(output_dir)
    dump_ws_events(output_dir)
    print("Done.")


if __name__ == "__main__":
    main()
