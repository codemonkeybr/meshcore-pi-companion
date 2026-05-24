"""Repository for fanout_configs table."""

import json
import logging
import time
import uuid
from typing import Any

import aiosqlite

from app.database import db

logger = logging.getLogger(__name__)

# In-memory cache of config metadata (name, type) for status reporting.
# Populated by get_all/get/create/update and read by FanoutManager.get_statuses().
_configs_cache: dict[str, dict[str, Any]] = {}


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a database row to a config dict."""
    result = {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "config": json.loads(row["config"]) if row["config"] else {},
        "scope": json.loads(row["scope"]) if row["scope"] else {},
        "sort_order": row["sort_order"] or 0,
        "created_at": row["created_at"] or 0,
    }
    _configs_cache[result["id"]] = result
    return result


async def _get_in_conn(conn: aiosqlite.Connection, config_id: str) -> dict[str, Any] | None:
    """Fetch a config using an already-acquired connection.

    Used by ``create`` and ``update`` to return the freshly-written row
    without re-entering the non-reentrant DB lock.
    """
    async with conn.execute("SELECT * FROM fanout_configs WHERE id = ?", (config_id,)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


class FanoutConfigRepository:
    """CRUD operations for fanout_configs table."""

    @staticmethod
    async def get_all() -> list[dict[str, Any]]:
        """Get all fanout configs ordered by sort_order."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM fanout_configs ORDER BY sort_order, created_at"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_dict(row) for row in rows]

    @staticmethod
    async def get(config_id: str) -> dict[str, Any] | None:
        """Get a single fanout config by ID."""
        async with db.readonly() as conn:
            return await _get_in_conn(conn, config_id)

    @staticmethod
    async def create(
        config_type: str,
        name: str,
        config: dict,
        scope: dict,
        enabled: bool = True,
        config_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new fanout config."""
        new_id = config_id or str(uuid.uuid4())
        now = int(time.time())

        async with db.tx() as conn:
            # Determine next sort_order under the same lock as the insert,
            # so two concurrent ``create()`` calls cannot collide.
            async with conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM fanout_configs"
            ) as cursor:
                row = await cursor.fetchone()
            sort_order = row[0] if row else 0

            async with conn.execute(
                """
                INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    config_type,
                    name,
                    1 if enabled else 0,
                    json.dumps(config),
                    json.dumps(scope),
                    sort_order,
                    now,
                ),
            ):
                pass

            result = await _get_in_conn(conn, new_id)
        assert result is not None
        return result

    @staticmethod
    async def update(config_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update a fanout config. Only provided fields are updated."""
        updates: list[str] = []
        params: list[Any] = []

        for field in ("name", "enabled", "config", "scope", "sort_order"):
            if field in fields:
                value = fields[field]
                if field == "enabled":
                    value = 1 if value else 0
                elif field in ("config", "scope"):
                    value = json.dumps(value)
                updates.append(f"{field} = ?")
                params.append(value)

        if not updates:
            return await FanoutConfigRepository.get(config_id)

        params.append(config_id)
        query = f"UPDATE fanout_configs SET {', '.join(updates)} WHERE id = ?"
        async with db.tx() as conn:
            async with conn.execute(query, params):
                pass
            return await _get_in_conn(conn, config_id)

    @staticmethod
    async def delete(config_id: str) -> None:
        """Delete a fanout config."""
        async with db.tx() as conn:
            async with conn.execute("DELETE FROM fanout_configs WHERE id = ?", (config_id,)):
                pass
        _configs_cache.pop(config_id, None)

    @staticmethod
    async def get_enabled() -> list[dict[str, Any]]:
        """Get all enabled fanout configs."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM fanout_configs WHERE enabled = 1 ORDER BY sort_order, created_at"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_dict(row) for row in rows]
