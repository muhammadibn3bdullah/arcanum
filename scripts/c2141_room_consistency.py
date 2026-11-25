#!/usr/bin/env python3
"""C2.1.4.1: canonical room consistency query after all test executions."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from arcanum.config.settings import Settings
from arcanum.memory.database import Database
from arcanum.runtime import rooms


def main() -> int:
    db = Database(Settings().db_path)
    rooms_rows = db.conn.execute("SELECT workflow_id, target_id, COUNT(*) AS n FROM domain_rooms GROUP BY workflow_id, target_id").fetchall()
    duplicate_rooms = [dict(r) for r in rooms_rows if r["n"] > 1]
    views = db.conn.execute("SELECT agent_id, room_id, COUNT(*) AS n FROM room_agent_views GROUP BY room_id, agent_id").fetchall()
    duplicate_views = [dict(v) for v in views if v["n"] > 1]
    msg_rows = db.conn.execute("SELECT idempotency_key, COUNT(*) AS n FROM canonical_messages GROUP BY idempotency_key").fetchall()
    duplicate_keys = [dict(r) for r in msg_rows if r["n"] > 1]
    total_messages = db.conn.execute("SELECT COUNT(*) AS n FROM canonical_messages").fetchone()["n"]
    cross_target = db.conn.execute(
        "SELECT COUNT(*) AS n FROM canonical_messages m "
        "JOIN domain_rooms r ON r.room_id=m.room_id "
        "WHERE m.workflow_id<>r.workflow_id OR m.target_id<>r.target_id"
    ).fetchone()["n"]
    senders = Counter(
        r["sender_agent_id"] for r in db.conn.execute("SELECT sender_agent_id FROM canonical_messages")
    )
    target_rooms = {}
    for r in db.conn.execute("SELECT DISTINCT target_id FROM domain_rooms"):
        target_rooms[r["target_id"]] = db.conn.execute(
            "SELECT room_id FROM domain_rooms WHERE target_id=?", (r["target_id"],)
        ).fetchall()
    db.close()

    summary = {
        "canonical_rooms": len(rooms_rows),
        "duplicate_room_mappings": duplicate_rooms,
        "duplicate_agent_views": duplicate_views,
        "canonical_messages": total_messages,
        "duplicate_idempotency_keys": duplicate_keys,
        "cross_target_messages": cross_target,
        "senders": dict(senders),
        "target_room_ids": {
            t: sorted({r["room_id"] for r in rows})
            for t, rows in sorted(target_rooms.items())
        },
        "maestro_tabs_not_in_room_table": "internal execution metadata; no user-facing room rows",
    }
    print(json.dumps(summary, indent=2))
    ok = (
        not duplicate_rooms and not duplicate_views and not duplicate_keys
        and cross_target == 0
        and all(len(ids) >= 1 for ids in summary["target_room_ids"].values())
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
