"""
Database migrations using SQLite's user_version pragma.

Migrations run automatically on startup. The user_version pragma tracks
which migrations have been applied (defaults to 0 for existing databases).

Each migration lives in its own file: ``_NNN_description.py``, exposing an
``async def migrate(conn)`` entry point.  The runner auto-discovers files by
numeric prefix and executes them in order.

This approach is safe for existing users - their databases have user_version=0,
so all migrations run in order on first startup after upgrade.
"""

import importlib
import logging
import pkgutil
import re

import aiosqlite

logger = logging.getLogger(__name__)


async def get_version(conn: aiosqlite.Connection) -> int:
    """Get current schema version from SQLite user_version pragma."""
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def set_version(conn: aiosqlite.Connection, version: int) -> None:
    """Set schema version using SQLite user_version pragma."""
    await conn.execute(f"PRAGMA user_version = {version}")


async def run_migrations(conn: aiosqlite.Connection) -> int:
    """
    Run all pending migrations.

    Returns the number of migrations applied.
    """
    version = await get_version(conn)
    applied = 0

    for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        match = re.match(r"_(\d+)_", module_info.name)
        if not match:
            continue
        num = int(match.group(1))
        if num <= version:
            continue
        logger.info("Applying migration %d: %s", num, module_info.name)
        mod = importlib.import_module(f"{__name__}.{module_info.name}")
        await mod.migrate(conn)
        await set_version(conn, num)
        applied += 1

    if applied > 0:
        logger.info(
            "Applied %d migration(s), schema now at version %d", applied, await get_version(conn)
        )
    else:
        logger.debug("Schema up to date at version %d", version)

    return applied
