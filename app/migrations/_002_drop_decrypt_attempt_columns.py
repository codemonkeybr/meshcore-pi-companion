import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Drop unused decrypt_attempts and last_attempt columns from raw_packets.

    These columns were added for a retry-limiting feature that was never implemented.
    They are written to but never read, so we can safely remove them.

    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    we silently skip (the columns will remain but are harmless).
    """
    for column in ["decrypt_attempts", "last_attempt"]:
        try:
            await conn.execute(f"ALTER TABLE raw_packets DROP COLUMN {column}")
            logger.debug("Dropped %s from raw_packets table", column)
        except aiosqlite.OperationalError as e:
            error_msg = str(e).lower()
            if "no such column" in error_msg:
                logger.debug("raw_packets.%s already dropped, skipping", column)
            elif "syntax error" in error_msg or "drop column" in error_msg:
                # SQLite version doesn't support DROP COLUMN - harmless, column stays
                logger.debug("SQLite doesn't support DROP COLUMN, %s column will remain", column)
            else:
                raise

    await conn.commit()
