import time
from typing import Any

from app.database import db
from app.models import (
    Contact,
    ContactAdvertPath,
    ContactAdvertPathSummary,
    ContactNameHistory,
)
from app.path_utils import first_hop_hex


class AmbiguousPublicKeyPrefixError(ValueError):
    """Raised when a public key prefix matches multiple contacts."""

    def __init__(self, prefix: str, matches: list[str]):
        self.prefix = prefix.lower()
        self.matches = matches
        super().__init__(f"Ambiguous public key prefix '{self.prefix}'")


class ContactRepository:
    @staticmethod
    async def upsert(contact: dict[str, Any]) -> None:
        await db.conn.execute(
            """
            INSERT INTO contacts (public_key, name, type, flags, last_path, last_path_len,
                                  last_advert, lat, lon, last_seen,
                                  on_radio, last_contacted, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(public_key) DO UPDATE SET
                name = COALESCE(excluded.name, contacts.name),
                type = CASE WHEN excluded.type = 0 THEN contacts.type ELSE excluded.type END,
                flags = excluded.flags,
                last_path = COALESCE(excluded.last_path, contacts.last_path),
                last_path_len = excluded.last_path_len,
                last_advert = COALESCE(excluded.last_advert, contacts.last_advert),
                lat = COALESCE(excluded.lat, contacts.lat),
                lon = COALESCE(excluded.lon, contacts.lon),
                last_seen = excluded.last_seen,
                on_radio = COALESCE(excluded.on_radio, contacts.on_radio),
                last_contacted = COALESCE(excluded.last_contacted, contacts.last_contacted),
                first_seen = COALESCE(contacts.first_seen, excluded.first_seen)
            """,
            (
                contact.get("public_key", "").lower(),
                contact.get("name"),
                contact.get("type", 0),
                contact.get("flags", 0),
                contact.get("last_path"),
                contact.get("last_path_len", -1),
                contact.get("last_advert"),
                contact.get("lat"),
                contact.get("lon"),
                contact.get("last_seen", int(time.time())),
                contact.get("on_radio"),
                contact.get("last_contacted"),
                contact.get("first_seen"),
            ),
        )
        await db.conn.commit()

    @staticmethod
    def _row_to_contact(row) -> Contact:
        """Convert a database row to a Contact model."""
        return Contact(
            public_key=row["public_key"],
            name=row["name"],
            type=row["type"],
            flags=row["flags"],
            last_path=row["last_path"],
            last_path_len=row["last_path_len"],
            last_advert=row["last_advert"],
            lat=row["lat"],
            lon=row["lon"],
            last_seen=row["last_seen"],
            on_radio=bool(row["on_radio"]),
            last_contacted=row["last_contacted"],
            last_read_at=row["last_read_at"],
            first_seen=row["first_seen"],
        )

    @staticmethod
    async def get_by_key(public_key: str) -> Contact | None:
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE public_key = ?", (public_key.lower(),)
        )
        row = await cursor.fetchone()
        return ContactRepository._row_to_contact(row) if row else None

    @staticmethod
    async def get_by_key_prefix(prefix: str) -> Contact | None:
        """Get a contact by key prefix only if it resolves uniquely.

        Returns None when no contacts match OR when multiple contacts match
        the prefix (to avoid silently selecting the wrong contact).
        """
        normalized_prefix = prefix.lower()
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE public_key LIKE ? ORDER BY public_key LIMIT 2",
            (f"{normalized_prefix}%",),
        )
        rows = list(await cursor.fetchall())
        if len(rows) != 1:
            return None
        return ContactRepository._row_to_contact(rows[0])

    @staticmethod
    async def _get_prefix_matches(prefix: str, limit: int = 2) -> list[Contact]:
        """Get contacts matching a key prefix, up to limit."""
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE public_key LIKE ? ORDER BY public_key LIMIT ?",
            (f"{prefix.lower()}%", limit),
        )
        rows = list(await cursor.fetchall())
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_by_key_or_prefix(key_or_prefix: str) -> Contact | None:
        """Get a contact by exact key match, falling back to prefix match.

        Useful when the input might be a full 64-char public key or a shorter prefix.
        """
        contact = await ContactRepository.get_by_key(key_or_prefix)
        if contact:
            return contact

        matches = await ContactRepository._get_prefix_matches(key_or_prefix, limit=2)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousPublicKeyPrefixError(
                key_or_prefix,
                [m.public_key for m in matches],
            )
        return None

    @staticmethod
    async def get_by_name(name: str) -> list[Contact]:
        """Get all contacts with the given exact name."""
        cursor = await db.conn.execute("SELECT * FROM contacts WHERE name = ?", (name,))
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def resolve_prefixes(prefixes: list[str]) -> dict[str, Contact]:
        """Resolve multiple key prefixes to contacts in a single query.

        Returns a dict mapping each prefix to its Contact, only for prefixes
        that resolve uniquely (exactly one match). Ambiguous or unmatched
        prefixes are omitted.
        """
        if not prefixes:
            return {}
        normalized = [p.lower() for p in prefixes]
        conditions = " OR ".join(["public_key LIKE ?"] * len(normalized))
        params = [f"{p}%" for p in normalized]
        cursor = await db.conn.execute(f"SELECT * FROM contacts WHERE {conditions}", params)
        rows = await cursor.fetchall()
        # Group by which prefix each row matches
        prefix_to_rows: dict[str, list] = {p: [] for p in normalized}
        for row in rows:
            pk = row["public_key"]
            for p in normalized:
                if pk.startswith(p):
                    prefix_to_rows[p].append(row)
        # Only include uniquely-resolved prefixes
        result: dict[str, Contact] = {}
        for p in normalized:
            if len(prefix_to_rows[p]) == 1:
                result[p] = ContactRepository._row_to_contact(prefix_to_rows[p][0])
        return result

    @staticmethod
    async def get_all(limit: int = 100, offset: int = 0) -> list[Contact]:
        cursor = await db.conn.execute(
            "SELECT * FROM contacts ORDER BY COALESCE(name, public_key) LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_recent_non_repeaters(limit: int = 200) -> list[Contact]:
        """Get the most recently active non-repeater contacts.

        Orders by most recent activity (last_contacted or last_advert),
        excluding repeaters (type=2).
        """
        cursor = await db.conn.execute(
            """
            SELECT * FROM contacts
            WHERE type != 2
            ORDER BY COALESCE(last_contacted, 0) DESC, COALESCE(last_advert, 0) DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def update_path(public_key: str, path: str, path_len: int) -> None:
        await db.conn.execute(
            """UPDATE contacts SET last_path = ?, last_path_len = ?,
               last_seen = ? WHERE public_key = ?""",
            (path, path_len, int(time.time()), public_key.lower()),
        )
        await db.conn.commit()

    @staticmethod
    async def set_on_radio(public_key: str, on_radio: bool) -> None:
        await db.conn.execute(
            "UPDATE contacts SET on_radio = ? WHERE public_key = ?",
            (on_radio, public_key.lower()),
        )
        await db.conn.commit()

    @staticmethod
    async def clear_on_radio_except(keep_keys: list[str]) -> None:
        """Set on_radio=False for all contacts NOT in keep_keys."""
        if not keep_keys:
            await db.conn.execute("UPDATE contacts SET on_radio = 0 WHERE on_radio = 1")
        else:
            placeholders = ",".join("?" * len(keep_keys))
            await db.conn.execute(
                f"UPDATE contacts SET on_radio = 0 WHERE on_radio = 1 AND public_key NOT IN ({placeholders})",
                keep_keys,
            )
        await db.conn.commit()

    @staticmethod
    async def delete(public_key: str) -> None:
        normalized = public_key.lower()
        await db.conn.execute(
            "DELETE FROM contact_name_history WHERE public_key = ?", (normalized,)
        )
        await db.conn.execute(
            "DELETE FROM contact_advert_paths WHERE public_key = ?", (normalized,)
        )
        await db.conn.execute("DELETE FROM contacts WHERE public_key = ?", (normalized,))
        await db.conn.commit()

    @staticmethod
    async def update_last_contacted(public_key: str, timestamp: int | None = None) -> None:
        """Update the last_contacted timestamp for a contact."""
        ts = timestamp if timestamp is not None else int(time.time())
        await db.conn.execute(
            "UPDATE contacts SET last_contacted = ?, last_seen = ? WHERE public_key = ?",
            (ts, ts, public_key.lower()),
        )
        await db.conn.commit()

    @staticmethod
    async def update_last_read_at(public_key: str, timestamp: int | None = None) -> bool:
        """Update the last_read_at timestamp for a contact.

        Returns True if a row was updated, False if contact not found.
        """
        ts = timestamp if timestamp is not None else int(time.time())
        cursor = await db.conn.execute(
            "UPDATE contacts SET last_read_at = ? WHERE public_key = ?",
            (ts, public_key.lower()),
        )
        await db.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    async def mark_all_read(timestamp: int) -> None:
        """Mark all contacts as read at the given timestamp."""
        await db.conn.execute("UPDATE contacts SET last_read_at = ?", (timestamp,))
        await db.conn.commit()

    @staticmethod
    async def get_by_pubkey_first_byte(hex_byte: str) -> list[Contact]:
        """Get contacts whose public key starts with the given hex byte (2 chars)."""
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE substr(public_key, 1, 2) = ?",
            (hex_byte.lower(),),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]


class ContactAdvertPathRepository:
    """Repository for recent unique advertisement paths per contact."""

    @staticmethod
    def _row_to_path(row) -> ContactAdvertPath:
        path = row["path_hex"] or ""
        path_len = row["path_len"]
        next_hop = first_hop_hex(path, path_len)
        return ContactAdvertPath(
            path=path,
            path_len=path_len,
            next_hop=next_hop,
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            heard_count=row["heard_count"],
        )

    @staticmethod
    async def record_observation(
        public_key: str,
        path_hex: str,
        timestamp: int,
        max_paths: int = 10,
        hop_count: int | None = None,
    ) -> None:
        """
        Upsert a unique advert path observation for a contact and prune to N most recent.
        """
        if max_paths < 1:
            max_paths = 1

        normalized_key = public_key.lower()
        normalized_path = path_hex.lower()
        path_len = hop_count if hop_count is not None else len(normalized_path) // 2

        await db.conn.execute(
            """
            INSERT INTO contact_advert_paths
                (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(public_key, path_hex) DO UPDATE SET
                last_seen = MAX(contact_advert_paths.last_seen, excluded.last_seen),
                path_len = excluded.path_len,
                heard_count = contact_advert_paths.heard_count + 1
            """,
            (normalized_key, normalized_path, path_len, timestamp, timestamp),
        )

        # Keep only the N most recent unique paths per contact.
        await db.conn.execute(
            """
            DELETE FROM contact_advert_paths
            WHERE public_key = ?
              AND path_hex NOT IN (
                  SELECT path_hex
                  FROM contact_advert_paths
                  WHERE public_key = ?
                  ORDER BY last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
                  LIMIT ?
              )
            """,
            (normalized_key, normalized_key, max_paths),
        )
        await db.conn.commit()

    @staticmethod
    async def get_recent_for_contact(public_key: str, limit: int = 10) -> list[ContactAdvertPath]:
        cursor = await db.conn.execute(
            """
            SELECT path_hex, path_len, first_seen, last_seen, heard_count
            FROM contact_advert_paths
            WHERE public_key = ?
            ORDER BY last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
            LIMIT ?
            """,
            (public_key.lower(), limit),
        )
        rows = await cursor.fetchall()
        return [ContactAdvertPathRepository._row_to_path(row) for row in rows]

    @staticmethod
    async def get_recent_for_all_contacts(
        limit_per_contact: int = 10,
    ) -> list[ContactAdvertPathSummary]:
        cursor = await db.conn.execute(
            """
            SELECT public_key, path_hex, path_len, first_seen, last_seen, heard_count
            FROM contact_advert_paths
            ORDER BY public_key ASC, last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
            """
        )
        rows = await cursor.fetchall()

        grouped: dict[str, list[ContactAdvertPath]] = {}
        for row in rows:
            key = row["public_key"]
            paths = grouped.get(key)
            if paths is None:
                paths = []
                grouped[key] = paths
            if len(paths) >= limit_per_contact:
                continue
            paths.append(ContactAdvertPathRepository._row_to_path(row))

        return [
            ContactAdvertPathSummary(public_key=key, paths=paths) for key, paths in grouped.items()
        ]


class ContactNameHistoryRepository:
    """Repository for contact name change history."""

    @staticmethod
    async def record_name(public_key: str, name: str, timestamp: int) -> None:
        """Record a name observation. Upserts: updates last_seen if name already known."""
        await db.conn.execute(
            """
            INSERT INTO contact_name_history (public_key, name, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(public_key, name) DO UPDATE SET
                last_seen = MAX(contact_name_history.last_seen, excluded.last_seen)
            """,
            (public_key.lower(), name, timestamp, timestamp),
        )
        await db.conn.commit()

    @staticmethod
    async def get_history(public_key: str) -> list[ContactNameHistory]:
        cursor = await db.conn.execute(
            """
            SELECT name, first_seen, last_seen
            FROM contact_name_history
            WHERE public_key = ?
            ORDER BY last_seen DESC
            """,
            (public_key.lower(),),
        )
        rows = await cursor.fetchall()
        return [
            ContactNameHistory(
                name=row["name"], first_seen=row["first_seen"], last_seen=row["last_seen"]
            )
            for row in rows
        ]
