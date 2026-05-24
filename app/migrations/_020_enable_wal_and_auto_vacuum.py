import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Enable WAL journal mode and incremental auto-vacuum.

    WAL (Write-Ahead Logging):
    - Faster writes: appends to a WAL file instead of rewriting the main DB
    - Concurrent reads during writes (readers don't block writers)
    - No journal file create/delete churn on every commit

    Incremental auto-vacuum:
    - Pages freed by DELETE become reclaimable without a full VACUUM
    - Call PRAGMA incremental_vacuum to reclaim on demand
    - Less overhead than FULL auto-vacuum (which reorganizes on every commit)

    auto_vacuum mode change requires a VACUUM to restructure the file.
    The VACUUM is performed before switching to WAL so it runs under the
    current journal mode; WAL is then set as the final step.
    """
    # Check current auto_vacuum mode
    cursor = await conn.execute("PRAGMA auto_vacuum")
    row = await cursor.fetchone()
    current_auto_vacuum = row[0] if row else 0

    if current_auto_vacuum != 2:  # 2 = INCREMENTAL
        logger.info("Switching auto_vacuum to INCREMENTAL (requires VACUUM)...")
        await conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        await conn.execute("VACUUM")
        logger.info("VACUUM complete, auto_vacuum set to INCREMENTAL")
    else:
        logger.debug("auto_vacuum already INCREMENTAL, skipping VACUUM")

    # Enable WAL mode (idempotent — returns current mode)
    cursor = await conn.execute("PRAGMA journal_mode = WAL")
    row = await cursor.fetchone()
    mode = row[0] if row else "unknown"
    logger.info("Journal mode set to %s", mode)

    await conn.commit()
