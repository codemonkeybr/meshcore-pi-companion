import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Seed the #remoteterm hashtag channel so new installs have it by default.

    Uses INSERT OR IGNORE so it's a no-op if the channel already exists
    (e.g. existing users who already added it manually). The channels table
    is created by the base schema before migrations run, so it always exists
    in production.
    """
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO channels (key, name, is_hashtag, on_radio) VALUES (?, ?, ?, ?)",
            ("8959AE053F2201801342A1DBDDA184F6", "#remoteterm", 1, 0),
        )
        await conn.commit()
    except Exception:
        logger.debug("Skipping #remoteterm seed (channels table not ready)")
