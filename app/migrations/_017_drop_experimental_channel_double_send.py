import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Drop experimental_channel_double_send column from app_settings.

    This feature is replaced by a user-triggered resend button.
    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    we silently skip (the column will remain but is unused).
    """
    try:
        await conn.execute("ALTER TABLE app_settings DROP COLUMN experimental_channel_double_send")
        logger.debug("Dropped experimental_channel_double_send from app_settings")
    except aiosqlite.OperationalError as e:
        error_msg = str(e).lower()
        if "no such column" in error_msg:
            logger.debug("app_settings.experimental_channel_double_send already dropped, skipping")
        elif "syntax error" in error_msg or "drop column" in error_msg:
            logger.debug(
                "SQLite doesn't support DROP COLUMN, "
                "experimental_channel_double_send column will remain"
            )
        else:
            raise

    await conn.commit()
