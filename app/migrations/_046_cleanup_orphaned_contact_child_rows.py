import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Move uniquely resolvable orphan contact child rows onto full contacts, drop the rest."""
    existing_tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await existing_tables_cursor.fetchall()}
    if "contacts" not in existing_tables:
        await conn.commit()
        return

    child_tables = [
        table
        for table in ("contact_name_history", "contact_advert_paths")
        if table in existing_tables
    ]
    if not child_tables:
        await conn.commit()
        return

    orphan_keys: set[str] = set()

    for table in child_tables:
        cursor = await conn.execute(
            f"""
            SELECT DISTINCT child.public_key
            FROM {table} child
            LEFT JOIN contacts c ON c.public_key = child.public_key
            WHERE c.public_key IS NULL
            """
        )
        orphan_keys.update(row[0] for row in await cursor.fetchall())

    for orphan_key in sorted(orphan_keys, key=len, reverse=True):
        match_cursor = await conn.execute(
            """
            SELECT public_key
            FROM contacts
            WHERE length(public_key) = 64
              AND public_key LIKE ? || '%'
            ORDER BY public_key
            """,
            (orphan_key.lower(),),
        )
        matches = [row[0] for row in await match_cursor.fetchall()]
        resolved_key = matches[0] if len(matches) == 1 else None

        if resolved_key is not None:
            if "contact_name_history" in child_tables:
                await conn.execute(
                    """
                    INSERT INTO contact_name_history (public_key, name, first_seen, last_seen)
                    SELECT ?, name, first_seen, last_seen
                    FROM contact_name_history
                    WHERE public_key = ?
                    ON CONFLICT(public_key, name) DO UPDATE SET
                        first_seen = MIN(contact_name_history.first_seen, excluded.first_seen),
                        last_seen = MAX(contact_name_history.last_seen, excluded.last_seen)
                    """,
                    (resolved_key, orphan_key),
                )
            if "contact_advert_paths" in child_tables:
                await conn.execute(
                    """
                    INSERT INTO contact_advert_paths
                        (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
                    SELECT ?, path_hex, path_len, first_seen, last_seen, heard_count
                    FROM contact_advert_paths
                    WHERE public_key = ?
                    ON CONFLICT(public_key, path_hex, path_len) DO UPDATE SET
                        first_seen = MIN(contact_advert_paths.first_seen, excluded.first_seen),
                        last_seen = MAX(contact_advert_paths.last_seen, excluded.last_seen),
                        heard_count = contact_advert_paths.heard_count + excluded.heard_count
                    """,
                    (resolved_key, orphan_key),
                )

        if "contact_name_history" in child_tables:
            await conn.execute(
                "DELETE FROM contact_name_history WHERE public_key = ?",
                (orphan_key,),
            )
        if "contact_advert_paths" in child_tables:
            await conn.execute(
                "DELETE FROM contact_advert_paths WHERE public_key = ?",
                (orphan_key,),
            )

    await conn.commit()
