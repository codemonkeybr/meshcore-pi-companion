"""Repository for push_subscriptions table."""

import logging
import time
import uuid
from typing import Any

from app.database import db

logger = logging.getLogger(__name__)

# Auto-delete subscriptions that have failed this many times consecutively
# without any successful delivery in between.
MAX_CONSECUTIVE_FAILURES = 15


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "endpoint": row["endpoint"],
        "p256dh": row["p256dh"],
        "auth": row["auth"],
        "label": row["label"] or "",
        "created_at": row["created_at"] or 0,
        "last_success_at": row["last_success_at"],
        "failure_count": row["failure_count"] or 0,
    }


class PushSubscriptionRepository:
    @staticmethod
    async def create(
        endpoint: str,
        p256dh: str,
        auth: str,
        label: str = "",
    ) -> dict[str, Any]:
        """Create or upsert a push subscription (keyed by endpoint)."""
        sub_id = str(uuid.uuid4())
        now = int(time.time())

        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO push_subscriptions
                    (id, endpoint, p256dh, auth, label, created_at, failure_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(endpoint) DO UPDATE SET
                    p256dh = excluded.p256dh,
                    auth = excluded.auth,
                    label = CASE WHEN excluded.label != '' THEN excluded.label
                                 ELSE push_subscriptions.label END,
                    failure_count = 0
                """,
                (sub_id, endpoint, p256dh, auth, label, now),
            )
            async with conn.execute(
                "SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            ) as cursor:
                row = await cursor.fetchone()

        return _row_to_dict(row) if row else {"id": sub_id}  # type: ignore[arg-type]

    @staticmethod
    async def get(subscription_id: str) -> dict[str, Any] | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM push_subscriptions WHERE id = ?", (subscription_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    async def get_by_endpoint(endpoint: str) -> dict[str, Any] | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    async def get_all() -> list[dict[str, Any]]:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM push_subscriptions ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_dict(row) for row in rows]

    @staticmethod
    async def update(subscription_id: str, **fields: Any) -> dict[str, Any] | None:
        updates: list[str] = []
        params: list[Any] = []

        if "label" in fields:
            updates.append("label = ?")
            params.append(fields["label"])

        if not updates:
            return await PushSubscriptionRepository.get(subscription_id)

        params.append(subscription_id)
        async with db.tx() as conn:
            await conn.execute(
                f"UPDATE push_subscriptions SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            async with conn.execute(
                "SELECT * FROM push_subscriptions WHERE id = ?", (subscription_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    async def delete(subscription_id: str) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,)
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def delete_by_endpoint(endpoint: str) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def batch_record_outcomes(
        success_ids: list[str], failure_ids: list[str], remove_ids: list[str]
    ) -> None:
        """Batch-update delivery outcomes in a single transaction."""
        now = int(time.time())
        async with db.tx() as conn:
            if remove_ids:
                placeholders = ",".join("?" for _ in remove_ids)
                await conn.execute(
                    f"DELETE FROM push_subscriptions WHERE id IN ({placeholders})",
                    remove_ids,
                )
            if success_ids:
                placeholders = ",".join("?" for _ in success_ids)
                await conn.execute(
                    f"UPDATE push_subscriptions SET last_success_at = ?, failure_count = 0 "
                    f"WHERE id IN ({placeholders})",
                    [now, *success_ids],
                )
            if failure_ids:
                placeholders = ",".join("?" for _ in failure_ids)
                await conn.execute(
                    f"UPDATE push_subscriptions SET failure_count = failure_count + 1 "
                    f"WHERE id IN ({placeholders})",
                    failure_ids,
                )
            # Evict subscriptions that have exceeded the failure threshold
            await conn.execute(
                "DELETE FROM push_subscriptions WHERE failure_count >= ?",
                (MAX_CONSECUTIVE_FAILURES,),
            )
