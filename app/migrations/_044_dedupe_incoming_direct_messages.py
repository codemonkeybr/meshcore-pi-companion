import json
import logging

import aiosqlite

logger = logging.getLogger(__name__)


def _merge_message_paths(paths_json_values: list[str | None]) -> str | None:
    """Merge multiple message path arrays into one exact-observation list."""
    merged: list[dict[str, object]] = []
    seen: set[tuple[object | None, object | None, object | None]] = set()

    for paths_json in paths_json_values:
        if not paths_json:
            continue
        try:
            parsed = json.loads(paths_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, list):
            continue
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            key = (
                entry.get("path"),
                entry.get("received_at"),
                entry.get("path_len"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)

    return json.dumps(merged) if merged else None


async def migrate(conn: aiosqlite.Connection) -> None:
    """Collapse same-contact same-text same-second incoming DMs into one row."""
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if await cursor.fetchone() is None:
        await conn.commit()
        return

    cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await cursor.fetchall()}
    required_columns = {
        "id",
        "type",
        "conversation_key",
        "text",
        "sender_timestamp",
        "received_at",
        "paths",
        "txt_type",
        "signature",
        "outgoing",
        "acked",
        "sender_name",
        "sender_key",
    }
    if not required_columns.issubset(columns):
        logger.debug("messages table missing incoming-DM dedup columns, skipping migration 44")
        await conn.commit()
        return

    raw_packets_cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_packets'"
    )
    raw_packets_exists = await raw_packets_cursor.fetchone() is not None

    duplicate_groups_cursor = await conn.execute(
        """
        SELECT conversation_key, text,
               COALESCE(sender_timestamp, 0) AS normalized_sender_timestamp,
               COUNT(*) AS duplicate_count
        FROM messages
        WHERE type = 'PRIV' AND outgoing = 0
        GROUP BY conversation_key, text, COALESCE(sender_timestamp, 0)
        HAVING COUNT(*) > 1
        """
    )
    duplicate_groups = await duplicate_groups_cursor.fetchall()

    for group in duplicate_groups:
        normalized_sender_timestamp = group["normalized_sender_timestamp"]
        rows_cursor = await conn.execute(
            """
            SELECT *
            FROM messages
            WHERE type = 'PRIV' AND outgoing = 0
              AND conversation_key = ? AND text = ?
              AND COALESCE(sender_timestamp, 0) = ?
            ORDER BY id ASC
            """,
            (
                group["conversation_key"],
                group["text"],
                normalized_sender_timestamp,
            ),
        )
        rows = list(await rows_cursor.fetchall())
        if len(rows) < 2:
            continue

        keeper = rows[0]
        duplicate_ids = [row["id"] for row in rows[1:]]
        merged_paths = _merge_message_paths([row["paths"] for row in rows])
        merged_received_at = min(row["received_at"] for row in rows)
        merged_txt_type = next((row["txt_type"] for row in rows if row["txt_type"] != 0), 0)
        merged_signature = next((row["signature"] for row in rows if row["signature"]), None)
        merged_sender_name = next((row["sender_name"] for row in rows if row["sender_name"]), None)
        merged_sender_key = next((row["sender_key"] for row in rows if row["sender_key"]), None)
        merged_acked = max(int(row["acked"] or 0) for row in rows)

        await conn.execute(
            """
            UPDATE messages
            SET received_at = ?, paths = ?, txt_type = ?, signature = ?,
                acked = ?, sender_name = ?, sender_key = ?
            WHERE id = ?
            """,
            (
                merged_received_at,
                merged_paths,
                merged_txt_type,
                merged_signature,
                merged_acked,
                merged_sender_name,
                merged_sender_key,
                keeper["id"],
            ),
        )

        if raw_packets_exists:
            for duplicate_id in duplicate_ids:
                await conn.execute(
                    "UPDATE raw_packets SET message_id = ? WHERE message_id = ?",
                    (keeper["id"], duplicate_id),
                )

        placeholders = ",".join("?" for _ in duplicate_ids)
        await conn.execute(
            f"DELETE FROM messages WHERE id IN ({placeholders})",
            duplicate_ids,
        )

    await conn.execute("DROP INDEX IF EXISTS idx_messages_incoming_priv_dedup")
    await conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_incoming_priv_dedup
           ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))
           WHERE type = 'PRIV' AND outgoing = 0"""
    )
    await conn.commit()
