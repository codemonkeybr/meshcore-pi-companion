import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Remove vestigial sidebar_sort_order column from app_settings."""
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "sidebar_sort_order" in columns:
        try:
            await conn.execute("ALTER TABLE app_settings DROP COLUMN sidebar_sort_order")
            await conn.commit()
        except Exception as e:
            error_msg = str(e).lower()
            if "syntax error" in error_msg or "drop column" in error_msg:
                logger.debug(
                    "SQLite doesn't support DROP COLUMN, sidebar_sort_order column will remain"
                )
                await conn.commit()
            else:
                raise
