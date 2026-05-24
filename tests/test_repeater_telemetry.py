"""Tests for repeater telemetry history: repository CRUD and embedded status response."""

import time

import pytest

from app.models import CONTACT_TYPE_REPEATER
from app.repository import (
    ContactRepository,
    RepeaterTelemetryRepository,
)

KEY_A = "aa" * 32
KEY_B = "bb" * 32

SAMPLE_STATUS = {
    "battery_volts": 4.15,
    "tx_queue_len": 0,
    "noise_floor_dbm": -100,
    "last_rssi_dbm": -80,
    "last_snr_db": 5.0,
    "packets_received": 100,
    "packets_sent": 50,
    "airtime_seconds": 300,
    "rx_airtime_seconds": 200,
    "uptime_seconds": 1000,
    "sent_flood": 10,
    "sent_direct": 40,
    "recv_flood": 60,
    "recv_direct": 40,
    "flood_dups": 5,
    "direct_dups": 2,
    "full_events": 0,
    "recv_errors": None,
}


async def _insert_repeater(public_key: str, name: str = "Repeater"):
    """Insert a repeater contact into the test database."""
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": CONTACT_TYPE_REPEATER,
            "flags": 0,
            "direct_path": None,
            "direct_path_len": -1,
            "direct_path_hash_mode": -1,
            "last_advert": None,
            "lat": None,
            "lon": None,
            "last_seen": None,
            "on_radio": False,
            "last_contacted": None,
            "first_seen": None,
        }
    )


@pytest.fixture
async def _db(test_db):
    """Set up test DB and patch the repeater_telemetry module's db reference."""
    from app.repository import repeater_telemetry

    original = repeater_telemetry.db
    repeater_telemetry.db = test_db
    try:
        yield test_db
    finally:
        repeater_telemetry.db = original


class TestRepeaterTelemetryRepository:
    """Tests for RepeaterTelemetryRepository CRUD operations with JSON blob storage."""

    @pytest.mark.asyncio
    async def test_record_and_get_history(self, _db):
        await _insert_repeater(KEY_A)
        now = int(time.time())

        await RepeaterTelemetryRepository.record(
            public_key=KEY_A,
            timestamp=now - 3600,
            data={**SAMPLE_STATUS, "battery_volts": 4.15},
        )
        await RepeaterTelemetryRepository.record(
            public_key=KEY_A,
            timestamp=now,
            data={**SAMPLE_STATUS, "battery_volts": 4.10},
        )

        history = await RepeaterTelemetryRepository.get_history(KEY_A, now - 7200)
        assert len(history) == 2
        assert history[0]["data"]["battery_volts"] == 4.15
        assert history[1]["data"]["battery_volts"] == 4.10
        assert history[0]["timestamp"] < history[1]["timestamp"]

    @pytest.mark.asyncio
    async def test_get_history_filters_by_time(self, _db):
        await _insert_repeater(KEY_A)
        now = int(time.time())

        await RepeaterTelemetryRepository.record(KEY_A, now - 7200, SAMPLE_STATUS)
        await RepeaterTelemetryRepository.record(KEY_A, now - 3600, SAMPLE_STATUS)
        await RepeaterTelemetryRepository.record(KEY_A, now, SAMPLE_STATUS)

        history = await RepeaterTelemetryRepository.get_history(KEY_A, now - 3601)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_get_history_isolates_by_key(self, _db):
        await _insert_repeater(KEY_A)
        await _insert_repeater(KEY_B)
        now = int(time.time())

        await RepeaterTelemetryRepository.record(
            KEY_A, now, {**SAMPLE_STATUS, "battery_volts": 4.1}
        )
        await RepeaterTelemetryRepository.record(
            KEY_B, now, {**SAMPLE_STATUS, "battery_volts": 3.9}
        )

        history_a = await RepeaterTelemetryRepository.get_history(KEY_A, 0)
        history_b = await RepeaterTelemetryRepository.get_history(KEY_B, 0)
        assert len(history_a) == 1
        assert len(history_b) == 1
        assert history_a[0]["data"]["battery_volts"] == 4.1

    @pytest.mark.asyncio
    async def test_data_stored_as_json(self, _db):
        """Verify the data column stores valid JSON that round-trips correctly."""
        await _insert_repeater(KEY_A)
        now = int(time.time())

        await RepeaterTelemetryRepository.record(KEY_A, now, SAMPLE_STATUS)
        history = await RepeaterTelemetryRepository.get_history(KEY_A, 0)
        assert len(history) == 1
        assert history[0]["data"] == SAMPLE_STATUS


class TestTelemetryHistoryEndpoint:
    """Tests for the read-only GET telemetry-history endpoint."""

    @pytest.mark.asyncio
    async def test_returns_history_for_repeater(self, _db, client):
        await _insert_repeater(KEY_A)
        now = int(time.time())
        await RepeaterTelemetryRepository.record(KEY_A, now, SAMPLE_STATUS)

        resp = await client.get(f"/api/contacts/{KEY_A}/repeater/telemetry-history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["data"]["battery_volts"] == 4.15

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_history(self, _db, client):
        await _insert_repeater(KEY_A)

        resp = await client.get(f"/api/contacts/{KEY_A}/repeater/telemetry-history")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_rejects_non_repeater(self, _db, client):
        await ContactRepository.upsert(
            {
                "public_key": KEY_A,
                "name": "Node",
                "type": 0,
                "flags": 0,
                "direct_path": None,
                "direct_path_len": -1,
                "direct_path_hash_mode": -1,
                "last_advert": None,
                "lat": None,
                "lon": None,
                "last_seen": None,
                "on_radio": False,
                "last_contacted": None,
                "first_seen": None,
            }
        )
        resp = await client.get(f"/api/contacts/{KEY_A}/repeater/telemetry-history")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_contact(self, _db, client):
        resp = await client.get(f"/api/contacts/{KEY_A}/repeater/telemetry-history")
        assert resp.status_code == 404
