#!/usr/bin/env python3
"""C2.1.4 real integration: one canonical room shared by all five agents."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from arcanum.config.settings import Settings  # noqa: E402
from arcanum.core.models import ScopeRef  # noqa: E402
from arcanum.core.rooms import AGENT_IDS, RoomMessageType  # noqa: E402
from arcanum.memory.database import Database  # noqa: E402
from arcanum.runtime import rooms  # noqa: E402


def seed_scopes(settings: Settings, targets: list[str]) -> None:
    db = Database(settings.db_path)
    for target in targets:
        if not db.find_scopes_by_domain(target.lower()):
            db.save_scope(ScopeRef(
                scope_ref=f"scope_{uuid.uuid4().hex[:10]}",
                domain=target,
                operator_id="local_operator",
                objective=f"Authorized shared-room test target {target}",
            ))
    db.close()


def http_json(path: str) -> dict:
    token = os.environ.get("DASHBOARD_TOKEN", "")
    url = f"http://127.0.0.1:13337{path}"
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def main() -> int:
    settings = Settings()
    workflow_id = f"wf_room_{uuid.uuid4().hex[:8]}"
    targets = ["SES_DED2", "domain-b.com"]
    seed_scopes(settings, targets)

    per_target: dict[str, dict] = {}
    for target in targets:
        room = rooms.get_or_create_domain_room(workflow_id, target, settings)
        for index, sender in enumerate(AGENT_IDS):
            rooms.publish_message(
                workflow_id, target, sender,
                RoomMessageType.TASK_ASSIGNMENT if index == 0 else RoomMessageType.STRUCTURED_RESULT,
                f"{sender} message for {target}",
                idempotency_key=f"{workflow_id}:{target}:{index}",
                settings=settings,
            )
        views = {}
        for agent in AGENT_IDS:
            view_room = rooms.get_room_for_agent(agent, workflow_id, target, settings)
            views[agent] = {
                "room_id": view_room.room_id if view_room else "",
                "messages": [
                    m["message_id"]
                    for m in rooms.room_messages(view_room.room_id, settings=settings)
                ] if view_room else [],
            }
        canonical = rooms.room_messages(room.room_id, settings=settings)
        per_target[target] = {
            "room_id": room.room_id,
            "canonical_messages": [m["message_id"] for m in canonical],
            "senders": [m["sender_agent_id"] for m in canonical],
            "views": views,
            "all_views_same_room": all(v["room_id"] == room.room_id for v in views.values()),
            "all_views_same_messages": all(
                v["messages"] == [m["message_id"] for m in canonical]
                for v in views.values()
            ),
        }

    # Dynamic third target: one new room, existing rooms unchanged.
    dynamic_target = "domain-c.com"
    seed_scopes(settings, [dynamic_target])
    dynamic_room = rooms.get_or_create_domain_room(workflow_id, dynamic_target, settings)
    rooms.publish_message(
        workflow_id, dynamic_target, "recon-agent", RoomMessageType.STRUCTURED_RESULT,
        "dynamic target result", idempotency_key=f"{workflow_id}:{dynamic_target}:0",
        settings=settings,
    )
    dynamic_views = {
        agent: rooms.get_room_for_agent(agent, workflow_id, dynamic_target, settings).room_id
        for agent in AGENT_IDS
    }

    # Dashboard API must report the same canonical rooms and message IDs.
    api_rooms = http_json("/api/rooms")
    api_messages = http_json(
        f"/api/rooms/messages?room_id={per_target[targets[0]]['room_id']}"
        "&agent_id=recon-agent"
    )

    count_db = Database(settings.db_path)
    room_count = len(count_db.list_rooms(workflow_id))
    count_db.close()
    summary = {
        "workflow_id": workflow_id,
        "canonical_room_count": room_count,
        "per_target": per_target,
        "dynamic": {
            "room_id": dynamic_room.room_id,
            "views": dynamic_views,
            "all_views_same_room": len(set(dynamic_views.values())) == 1,
        },
        "dashboard": {
            "rooms_reported": len(api_rooms.get("rooms", [])),
            "agents_reported": api_rooms.get("agents", []),
            "message_ids": [m["message_id"] for m in api_messages.get("messages", [])],
            "room_id": api_messages.get("room_id"),
            "agent_id": api_messages.get("agent_id"),
        },
    }
    print(json.dumps(summary, indent=2))

    first = per_target[targets[0]]
    ok = (
        all(t["all_views_same_room"] and t["all_views_same_messages"]
            for t in per_target.values())
        and len({t["room_id"] for t in per_target.values()}) == 2
        and summary["dynamic"]["all_views_same_room"]
        and summary["dashboard"]["room_id"] == first["room_id"]
        and summary["dashboard"]["message_ids"] == first["canonical_messages"]
        and set(summary["dashboard"]["agents_reported"]) == set(AGENT_IDS)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
