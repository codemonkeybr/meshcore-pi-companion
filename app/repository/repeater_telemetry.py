import json
import logging
import time

from app.database import db

logger = logging.getLogger(__name__)

# Maximum age for telemetry history entries (30 days)
_MAX_AGE_SECONDS = 30 * 86400

# Maximum entries to keep per repeater (sanity cap)
_MAX_ENTRIES_PER_REPEATER = 1000


class RepeaterTelemetryRepository:
    @staticmethod
    async def record(
        public_key: str,
        timestamp: int,
        data: dict,
    ) -> None:
        """Insert a telemetry history row and prune stale entries."""
        cutoff = int(time.time()) - _MAX_AGE_SECONDS
        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO repeater_telemetry_history
                    (public_key, timestamp, data)
                VALUES (?, ?, ?)
                """,
                (public_key, timestamp, json.dumps(data)),
            ):
                pass

            # Prune entries older than 30 days
            async with conn.execute(
                "DELETE FROM repeater_telemetry_history WHERE public_key = ? AND timestamp < ?",
                (public_key, cutoff),
            ):
                pass

            # Cap at _MAX_ENTRIES_PER_REPEATER (keep newest)
            async with conn.execute(
                """
                DELETE FROM repeater_telemetry_history
                WHERE public_key = ? AND id NOT IN (
                    SELECT id FROM repeater_telemetry_history
                    WHERE public_key = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                )
                """,
                (public_key, public_key, _MAX_ENTRIES_PER_REPEATER),
            ):
                pass

    @staticmethod
    async def get_history(public_key: str, since_timestamp: int) -> list[dict]:
        """Return telemetry rows for a repeater since a given timestamp, ordered ASC."""
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT timestamp, data
                FROM repeater_telemetry_history
                WHERE public_key = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (public_key, since_timestamp),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "timestamp": row["timestamp"],
                "data": json.loads(row["data"]),
            }
            for row in rows
        ]

    @staticmethod
    async def get_latest(public_key: str) -> dict | None:
        """Return the most recent telemetry row for a repeater, or None."""
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT timestamp, data
                FROM repeater_telemetry_history
                WHERE public_key = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (public_key,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "timestamp": row["timestamp"],
            "data": json.loads(row["data"]),
        }
