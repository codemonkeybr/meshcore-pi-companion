import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Replace legacy contact route columns with canonical direct-route columns."""
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if await cursor.fetchone() is None:
        await conn.commit()
        return

    cursor = await conn.execute("PRAGMA table_info(contacts)")
    columns = {row[1] for row in await cursor.fetchall()}

    target_columns = {
        "public_key",
        "name",
        "type",
        "flags",
        "direct_path",
        "direct_path_len",
        "direct_path_hash_mode",
        "direct_path_updated_at",
        "route_override_path",
        "route_override_len",
        "route_override_hash_mode",
        "last_advert",
        "lat",
        "lon",
        "last_seen",
        "on_radio",
        "last_contacted",
        "first_seen",
        "last_read_at",
    }
    if (
        target_columns.issubset(columns)
        and "last_path" not in columns
        and "out_path_hash_mode" not in columns
    ):
        await conn.commit()
        return

    await conn.execute(
        """
        CREATE TABLE contacts_new (
            public_key TEXT PRIMARY KEY,
            name TEXT,
            type INTEGER DEFAULT 0,
            flags INTEGER DEFAULT 0,
            direct_path TEXT,
            direct_path_len INTEGER,
            direct_path_hash_mode INTEGER,
            direct_path_updated_at INTEGER,
            route_override_path TEXT,
            route_override_len INTEGER,
            route_override_hash_mode INTEGER,
            last_advert INTEGER,
            lat REAL,
            lon REAL,
            last_seen INTEGER,
            on_radio INTEGER DEFAULT 0,
            last_contacted INTEGER,
            first_seen INTEGER,
            last_read_at INTEGER
        )
        """
    )

    select_expr = {
        "public_key": "public_key",
        "name": "NULL",
        "type": "0",
        "flags": "0",
        "direct_path": "NULL",
        "direct_path_len": "NULL",
        "direct_path_hash_mode": "NULL",
        "direct_path_updated_at": "NULL",
        "route_override_path": "NULL",
        "route_override_len": "NULL",
        "route_override_hash_mode": "NULL",
        "last_advert": "NULL",
        "lat": "NULL",
        "lon": "NULL",
        "last_seen": "NULL",
        "on_radio": "0",
        "last_contacted": "NULL",
        "first_seen": "NULL",
        "last_read_at": "NULL",
    }
    for name in ("name", "type", "flags"):
        if name in columns:
            select_expr[name] = name

    if "direct_path" in columns:
        select_expr["direct_path"] = "direct_path"

    if "direct_path_len" in columns:
        select_expr["direct_path_len"] = "direct_path_len"

    if "direct_path_hash_mode" in columns:
        select_expr["direct_path_hash_mode"] = "direct_path_hash_mode"

    for name in (
        "route_override_path",
        "route_override_len",
        "route_override_hash_mode",
        "last_advert",
        "lat",
        "lon",
        "last_seen",
        "on_radio",
        "last_contacted",
        "first_seen",
        "last_read_at",
    ):
        if name in columns:
            select_expr[name] = name

    ordered_columns = list(select_expr.keys())
    await conn.execute(
        f"""
        INSERT INTO contacts_new ({", ".join(ordered_columns)})
        SELECT {", ".join(select_expr[name] for name in ordered_columns)}
        FROM contacts
        """
    )

    await conn.execute("DROP TABLE contacts")
    await conn.execute("ALTER TABLE contacts_new RENAME TO contacts")
    await conn.commit()
