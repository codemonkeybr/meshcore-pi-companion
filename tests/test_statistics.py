"""Tests for the statistics repository and endpoint."""

import time

import pytest

from app.repository import StatisticsRepository


class TestStatisticsEmpty:
    @pytest.mark.asyncio
    async def test_empty_database(self, test_db):
        """All counts should be zero on an empty database."""
        result = await StatisticsRepository.get_all()

        assert result["contact_count"] == 0
        assert result["repeater_count"] == 0
        assert result["channel_count"] == 1  # #remoteterm seed from migration 33
        assert result["total_packets"] == 0
        assert result["decrypted_packets"] == 0
        assert result["undecrypted_packets"] == 0
        assert result["total_dms"] == 0
        assert result["total_channel_messages"] == 0
        assert result["total_outgoing"] == 0
        assert result["busiest_channels_24h"] == []
        assert result["contacts_heard"]["last_hour"] == 0
        assert result["contacts_heard"]["last_24_hours"] == 0
        assert result["contacts_heard"]["last_week"] == 0
        assert result["repeaters_heard"]["last_hour"] == 0
        assert result["repeaters_heard"]["last_24_hours"] == 0
        assert result["repeaters_heard"]["last_week"] == 0


class TestStatisticsCounts:
    @pytest.mark.asyncio
    async def test_counts_contacts_and_repeaters(self, test_db):
        """Contacts and repeaters are counted separately by type."""
        now = int(time.time())
        conn = test_db.conn
        # type=1 is client, type=2 is repeater
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("aa" * 32, 1, now),
        )
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("bb" * 32, 1, now),
        )
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("cc" * 32, 2, now),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["contact_count"] == 2
        assert result["repeater_count"] == 1

    @pytest.mark.asyncio
    async def test_channel_count(self, test_db):
        conn = test_db.conn
        await conn.execute(
            "INSERT INTO channels (key, name) VALUES (?, ?)",
            ("AA" * 16, "test-chan"),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        assert result["channel_count"] == 2  # test-chan + #remoteterm seed

    @pytest.mark.asyncio
    async def test_message_type_counts(self, test_db):
        """DM, channel, and outgoing messages are counted correctly."""
        now = int(time.time())
        conn = test_db.conn
        # 2 DMs, 3 channel messages, 1 outgoing
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("PRIV", "aa" * 32, "dm1", now, 0),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("PRIV", "bb" * 32, "dm2", now, 0),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("CHAN", "CC" * 16, "ch1", now, 0),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("CHAN", "CC" * 16, "ch2", now, 0),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("CHAN", "DD" * 16, "ch3", now, 1),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["total_dms"] == 2
        assert result["total_channel_messages"] == 3
        assert result["total_outgoing"] == 1

    @pytest.mark.asyncio
    async def test_packet_split(self, test_db):
        """Packets are split into decrypted and undecrypted."""
        now = int(time.time())
        conn = test_db.conn
        # Insert a message to link to
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", "AA" * 16, "msg", now),
        )
        msg_id = (await (await conn.execute("SELECT last_insert_rowid() AS id")).fetchone())["id"]

        # 2 decrypted packets (linked to message), 1 undecrypted
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?, ?, ?, ?)",
            (now, b"\x01", msg_id, b"\x01" * 32),
        )
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?, ?, ?, ?)",
            (now, b"\x02", msg_id, b"\x02" * 32),
        )
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
            (now, b"\x03", b"\x03" * 32),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["total_packets"] == 3
        assert result["decrypted_packets"] == 2
        assert result["undecrypted_packets"] == 1


class TestBusiestChannels:
    @pytest.mark.asyncio
    async def test_busiest_channels_returns_top_5(self, test_db):
        """Only the top 5 channels are returned, ordered by message count."""
        now = int(time.time())
        conn = test_db.conn

        # Create 6 channels with varying message counts
        for i in range(6):
            key = f"{i:02X}" * 16
            await conn.execute(
                "INSERT INTO channels (key, name) VALUES (?, ?)",
                (key, f"chan-{i}"),
            )
            for j in range(i + 1):
                await conn.execute(
                    "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
                    ("CHAN", key, f"msg-{j}", now),
                )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert len(result["busiest_channels_24h"]) == 5
        # Most messages first
        counts = [ch["message_count"] for ch in result["busiest_channels_24h"]]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] == 6  # channel 5 has 6 messages

    @pytest.mark.asyncio
    async def test_busiest_channels_excludes_old_messages(self, test_db):
        """Messages older than 24h are not counted."""
        now = int(time.time())
        old = now - 90000  # older than 24h
        conn = test_db.conn

        key = "AA" * 16
        await conn.execute("INSERT INTO channels (key, name) VALUES (?, ?)", (key, "old-chan"))
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", key, "old-msg", old),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        assert result["busiest_channels_24h"] == []

    @pytest.mark.asyncio
    async def test_busiest_channels_shows_key_when_no_channel_name(self, test_db):
        """When channel has no name in channels table, conversation_key is used."""
        now = int(time.time())
        conn = test_db.conn

        key = "FF" * 16
        # Don't insert into channels table
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", key, "msg", now),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        assert len(result["busiest_channels_24h"]) == 1
        assert result["busiest_channels_24h"][0]["channel_name"] == key


class TestActivityWindows:
    @pytest.mark.asyncio
    async def test_activity_windows(self, test_db):
        """Contacts are bucketed into time windows based on last_seen."""
        now = int(time.time())
        conn = test_db.conn

        # Contact seen 30 min ago (within 1h, 24h, 7d)
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("aa" * 32, 1, now - 1800),
        )
        # Contact seen 12h ago (within 24h, 7d but not 1h)
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("bb" * 32, 1, now - 43200),
        )
        # Contact seen 3 days ago (within 7d but not 1h or 24h)
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("cc" * 32, 1, now - 259200),
        )
        # Contact seen 10 days ago (outside all windows)
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("dd" * 32, 1, now - 864000),
        )
        # Repeater seen 30 min ago
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("ee" * 32, 2, now - 1800),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["contacts_heard"]["last_hour"] == 1
        assert result["contacts_heard"]["last_24_hours"] == 2
        assert result["contacts_heard"]["last_week"] == 3

        assert result["repeaters_heard"]["last_hour"] == 1
        assert result["repeaters_heard"]["last_24_hours"] == 1
        assert result["repeaters_heard"]["last_week"] == 1
