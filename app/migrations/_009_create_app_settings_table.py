import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Create app_settings table for persistent application preferences.

    This table stores:
    - max_radio_contacts: Configured radio contact capacity baseline for maintenance thresholds
    - favorites: JSON array of favorite conversations [{type, id}, ...]
    - auto_decrypt_dm_on_advert: Whether to attempt historical DM decryption on new contact
    - sidebar_sort_order: 'recent' or 'alpha' for sidebar sorting
    - last_message_times: JSON object mapping conversation keys to timestamps
    - preferences_migrated: Flag to track if localStorage has been migrated

    The table uses a single-row pattern (id=1) for simplicity.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            max_radio_contacts INTEGER DEFAULT 200,
            favorites TEXT DEFAULT '[]',
            auto_decrypt_dm_on_advert INTEGER DEFAULT 1,
            sidebar_sort_order TEXT DEFAULT 'recent',
            last_message_times TEXT DEFAULT '{}',
            preferences_migrated INTEGER DEFAULT 0
        )
        """
    )

    # Initialize with default row (use only the id column so this works
    # regardless of which columns exist — defaults fill the rest).
    await conn.execute("INSERT OR IGNORE INTO app_settings (id) VALUES (1)")

    await conn.commit()
    logger.debug("Created app_settings table with default values")
