import json
import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add region-scope decoding support.

    1. Add ``transport_code`` (uint16) and ``region`` columns to ``messages`` so
       transport-routed channel messages persist their resolved region even after
       the source raw packet is purged by maintenance.
    2. Add ``known_regions`` (JSON list) to ``app_settings`` and seed it from any
       region names we already know about: the global ``flood_scope`` and per-channel
       ``flood_scope_override`` values. Names are stored without the ``#`` prefix.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await tables_cursor.fetchall()}

    # --- messages columns ---
    if "messages" in existing_tables:
        col_cursor = await conn.execute("PRAGMA table_info(messages)")
        message_columns = {row[1] for row in await col_cursor.fetchall()}
        if "transport_code" not in message_columns:
            await conn.execute("ALTER TABLE messages ADD COLUMN transport_code INTEGER")
        if "region" not in message_columns:
            await conn.execute("ALTER TABLE messages ADD COLUMN region TEXT")
        await conn.commit()

    # --- app_settings.known_regions column + seed ---
    if "app_settings" not in existing_tables:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    settings_columns = {row[1] for row in await col_cursor.fetchall()}
    if "known_regions" not in settings_columns:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN known_regions TEXT")
        await conn.commit()

    def _clean(name: str | None) -> str | None:
        stripped = (name or "").strip()
        if stripped.startswith("#"):
            stripped = stripped[1:].strip()
        return stripped or None

    seeded: list[str] = []
    seen: set[str] = set()

    def _add(name: str | None) -> None:
        cleaned = _clean(name)
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            seeded.append(cleaned)

    # Global flood scope
    try:
        cursor = await conn.execute("SELECT flood_scope FROM app_settings WHERE id = 1")
        row = await cursor.fetchone()
        if row:
            _add(row[0])
    except aiosqlite.Error:
        pass

    # Per-channel flood scope overrides
    if "channels" in existing_tables:
        chan_cols_cursor = await conn.execute("PRAGMA table_info(channels)")
        chan_cols = {row[1] for row in await chan_cols_cursor.fetchall()}
        if "flood_scope_override" in chan_cols:
            cursor = await conn.execute(
                "SELECT DISTINCT flood_scope_override FROM channels "
                "WHERE flood_scope_override IS NOT NULL AND flood_scope_override != ''"
            )
            for row in await cursor.fetchall():
                _add(row[0])

    await conn.execute(
        "UPDATE app_settings SET known_regions = ? WHERE id = 1",
        (json.dumps(seeded),),
    )
    await conn.commit()

    if seeded:
        logger.info("Seeded %d known region(s) for scope decoding: %s", len(seeded), seeded)
