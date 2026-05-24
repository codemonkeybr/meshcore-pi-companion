"""Unit tests for the MapUploadModule fanout module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.fanout.map_upload import (
    _DEFAULT_API_URL,
    _REUPLOAD_SECONDS,
    MapUploadModule,
    _get_radio_params,
    _haversine_km,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module(config: dict | None = None) -> MapUploadModule:
    cfg = {"dry_run": True, "api_url": ""}
    if config:
        cfg.update(config)
    return MapUploadModule("test-id", cfg, name="Test Map Upload")


def _advert_raw_data(payload_type: str = "ADVERT", raw_hex: str = "aabbccdd") -> dict:
    return {
        "payload_type": payload_type,
        "data": raw_hex,
        "timestamp": 1000,
        "id": 1,
        "observation_id": 1,
    }


def _fake_advert(
    device_role: int = 2,
    timestamp: int = 2000,
    pubkey: str | None = None,
    lat: float | None = 51.5,
    lon: float | None = -0.1,
) -> MagicMock:
    advert = MagicMock()
    advert.device_role = device_role
    advert.timestamp = timestamp
    advert.public_key = pubkey or "ab" * 32
    advert.lat = lat
    advert.lon = lon
    return advert


# ---------------------------------------------------------------------------
# Module lifecycle
# ---------------------------------------------------------------------------


class TestMapUploadLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_client(self):
        mod = _make_module()
        await mod.start()
        assert mod._client is not None
        assert mod._client.follow_redirects is True
        assert mod.status == "connected"
        await mod.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_client(self):
        mod = _make_module()
        await mod.start()
        mod._last_error = "HTTP 500"  # simulate a prior error
        await mod.stop()
        assert mod._client is None
        assert mod._last_error is None
        assert mod.status == "disconnected"

    @pytest.mark.asyncio
    async def test_start_clears_seen_table(self):
        mod = _make_module()
        mod._seen["somepubkey"] = 999
        await mod.start()
        assert mod._seen == {}
        await mod.stop()

    def test_status_error_when_last_error_set(self):
        mod = _make_module()
        mod._client = MagicMock()
        mod._last_error = "HTTP 500"
        assert mod.status == "error"


# ---------------------------------------------------------------------------
# on_raw filtering
# ---------------------------------------------------------------------------


class TestOnRawFiltering:
    @pytest.mark.asyncio
    async def test_non_advert_packet_ignored(self):
        mod = _make_module()
        await mod.start()

        with patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload:
            await mod.on_raw(_advert_raw_data(payload_type="GROUP_TEXT"))
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_empty_data_ignored(self):
        mod = _make_module()
        await mod.start()

        with patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload:
            await mod.on_raw({"payload_type": "ADVERT", "data": ""})
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_invalid_hex_ignored(self):
        mod = _make_module()
        await mod.start()

        with patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload:
            await mod.on_raw({"payload_type": "ADVERT", "data": "ZZZZ"})
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_parse_failure_ignored(self):
        mod = _make_module()
        await mod.start()

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=None),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_advert_parse_failure_ignored(self):
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 10

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch("app.fanout.map_upload.parse_advertisement", return_value=None),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_chat_advert_skipped(self):
        """device_role == 1 (CHAT) must be skipped."""
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=1),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_sensor_advert_skipped(self):
        """device_role == 4 (Sensor) must be skipped."""
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=4),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_repeater_advert_processed(self):
        """device_role == 2 (Repeater) must be uploaded."""
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_called_once()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_room_advert_processed(self):
        """device_role == 3 (Room) must be uploaded."""
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=3),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_called_once()

        await mod.stop()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_first_seen_pubkey_passes(self):
        mod = _make_module()
        await mod.start()

        pubkey = "ab" * 32
        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2, timestamp=5000, pubkey=pubkey),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_called_once()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_replay_skipped(self):
        """Same or older timestamp should be skipped."""
        mod = _make_module()
        await mod.start()

        pubkey = "ab" * 32
        mod._seen[pubkey] = 5000  # already uploaded at ts=5000

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2, timestamp=5000, pubkey=pubkey),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_within_rate_limit_window_skipped(self):
        """Newer timestamp but within 1-hr window should be skipped."""
        mod = _make_module()
        await mod.start()

        pubkey = "ab" * 32
        last_ts = 5000
        mod._seen[pubkey] = last_ts

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        # 30 minutes later — still within the 1-hour window
        new_ts = last_ts + (_REUPLOAD_SECONDS // 2)

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2, timestamp=new_ts, pubkey=pubkey),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_after_rate_limit_window_passes(self):
        """Timestamp beyond the 1-hr window should be uploaded again."""
        mod = _make_module()
        await mod.start()

        pubkey = "ab" * 32
        last_ts = 5000
        mod._seen[pubkey] = last_ts

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        new_ts = last_ts + _REUPLOAD_SECONDS + 1

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2, timestamp=new_ts, pubkey=pubkey),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_called_once()

        await mod.stop()


# ---------------------------------------------------------------------------
# Dry run behaviour
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_logs_but_does_not_post(self):
        """dry_run=True must log the payload but never call httpx."""
        mod = _make_module({"dry_run": True})
        await mod.start()

        fake_private = bytes(range(64))
        fake_public = bytes(range(32))

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 915, "cr": 5, "sf": 10, "bw": 125},
            ),
        ):
            assert mod._client is not None
            post_mock = AsyncMock()
            mod._client.post = post_mock  # type: ignore[method-assign]

            await mod._upload("ab" * 32, 1000, 2, "aabbccdd", 0.0, 0.0)

            post_mock.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_dry_run_updates_seen_table(self):
        """dry_run still records the pubkey so rate-limiting works."""
        mod = _make_module({"dry_run": True})
        await mod.start()

        pubkey = "ab" * 32
        fake_private = bytes(range(64))
        fake_public = bytes(range(32))

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 0, "cr": 0, "sf": 0, "bw": 0},
            ),
        ):
            await mod._upload(pubkey, 9999, 2, "aabb", 0.0, 0.0)
            assert mod._seen[pubkey] == 9999

        await mod.stop()

    @pytest.mark.asyncio
    async def test_dry_run_no_key_logs_warning_and_returns(self):
        """If private key is missing, upload should log a warning and not crash."""
        mod = _make_module({"dry_run": True})
        await mod.start()

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=None),
            patch("app.fanout.map_upload.get_public_key", return_value=None),
        ):
            # Should not raise
            await mod._upload("ab" * 32, 1000, 2, "aabb", 0.0, 0.0)
            assert mod._seen == {}

        await mod.stop()


# ---------------------------------------------------------------------------
# Live send behaviour
# ---------------------------------------------------------------------------


class TestLiveSend:
    @pytest.mark.asyncio
    async def test_live_send_posts_to_api_url(self):
        """dry_run=False should POST to the configured api_url."""
        custom_url = "https://custom.example.com/api/upload"
        mod = _make_module({"dry_run": False, "api_url": custom_url})
        await mod.start()

        fake_private = bytes(range(64))
        fake_public = bytes(range(32))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 915, "cr": 5, "sf": 10, "bw": 125},
            ),
        ):
            assert mod._client is not None
            post_mock = AsyncMock(return_value=mock_response)
            mod._client.post = post_mock  # type: ignore[method-assign]

            await mod._upload("ab" * 32, 1000, 2, "aabbccdd", 0.0, 0.0)

            post_mock.assert_called_once()
            call_url = post_mock.call_args[0][0]
            assert call_url == custom_url

        await mod.stop()

    @pytest.mark.asyncio
    async def test_live_send_defaults_to_map_url(self):
        """Empty api_url should default to the map.meshcore.io endpoint."""
        mod = _make_module({"dry_run": False, "api_url": ""})
        await mod.start()

        fake_private = bytes(range(64))
        fake_public = bytes(range(32))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 0, "cr": 0, "sf": 0, "bw": 0},
            ),
        ):
            assert mod._client is not None
            post_mock = AsyncMock(return_value=mock_response)
            mod._client.post = post_mock  # type: ignore[method-assign]

            await mod._upload("ab" * 32, 1000, 2, "aabb", 0.0, 0.0)

            call_url = post_mock.call_args[0][0]
            assert call_url == _DEFAULT_API_URL

        await mod.stop()

    @pytest.mark.asyncio
    async def test_live_send_updates_seen_on_success(self):
        mod = _make_module({"dry_run": False})
        await mod.start()

        pubkey = "cd" * 32
        fake_private = bytes(range(64))
        fake_public = bytes(range(32))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 0, "cr": 0, "sf": 0, "bw": 0},
            ),
        ):
            assert mod._client is not None
            mod._client.post = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]
            await mod._upload(pubkey, 7777, 2, "aabb", 0.0, 0.0)
            assert mod._seen[pubkey] == 7777

        await mod.stop()

    @pytest.mark.asyncio
    async def test_live_send_http_error_sets_last_error(self):
        import httpx

        mod = _make_module({"dry_run": False})
        await mod.start()

        fake_private = bytes(range(64))
        fake_public = bytes(range(32))

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=error_response)

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 0, "cr": 0, "sf": 0, "bw": 0},
            ),
        ):
            assert mod._client is not None
            mod._client.post = AsyncMock(side_effect=exc)  # type: ignore[method-assign]
            await mod._upload("ab" * 32, 1000, 2, "aabb", 0.0, 0.0)
            assert mod._last_error == "HTTP 500"
            assert mod.status == "error"

        await mod.stop()

    @pytest.mark.asyncio
    async def test_live_send_request_error_sets_last_error(self):
        import httpx

        mod = _make_module({"dry_run": False})
        await mod.start()

        fake_private = bytes(range(64))
        fake_public = bytes(range(32))

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 0, "cr": 0, "sf": 0, "bw": 0},
            ),
        ):
            assert mod._client is not None
            mod._client.post = AsyncMock(side_effect=httpx.ConnectError("conn refused"))  # type: ignore[method-assign]
            await mod._upload("ab" * 32, 1000, 2, "aabb", 0.0, 0.0)
            assert mod._last_error is not None
            assert mod.status == "error"

        await mod.stop()


# ---------------------------------------------------------------------------
# Payload structure
# ---------------------------------------------------------------------------


class TestPayloadStructure:
    @pytest.mark.asyncio
    async def test_request_payload_has_required_fields(self):
        """The POST body must contain data, signature, and publicKey."""
        mod = _make_module({"dry_run": False})
        await mod.start()

        fake_private = bytes(range(64))
        fake_public = bytes(range(32))
        captured: list[dict] = []

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        async def capture_post(url, *, content, headers):
            captured.append(json.loads(content))
            return mock_response

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 915, "cr": 5, "sf": 10, "bw": 125},
            ),
        ):
            assert mod._client is not None
            mod._client.post = capture_post  # type: ignore[method-assign]
            await mod._upload("ab" * 32, 1000, 2, "aabbccdd", 0.0, 0.0)

        assert len(captured) == 1
        payload = captured[0]
        assert "data" in payload
        assert "signature" in payload
        assert "publicKey" in payload

        # data field should be parseable JSON with params and links
        inner = json.loads(payload["data"])
        assert "params" in inner
        assert "links" in inner
        assert len(inner["links"]) == 1
        assert inner["links"][0] == "meshcore://aabbccdd"

        # links reference the raw hex as-is
        assert inner["params"]["freq"] == 915
        assert inner["params"]["sf"] == 10

        await mod.stop()

    @pytest.mark.asyncio
    async def test_public_key_hex_in_payload(self):
        mod = _make_module({"dry_run": False})
        await mod.start()

        fake_private = bytes(range(64))
        fake_public = bytes(range(32))
        captured: list[dict] = []

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        async def capture_post(url, *, content, headers):
            captured.append(json.loads(content))
            return mock_response

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=fake_private),
            patch("app.fanout.map_upload.get_public_key", return_value=fake_public),
            patch(
                "app.fanout.map_upload._get_radio_params",
                return_value={"freq": 0, "cr": 0, "sf": 0, "bw": 0},
            ),
        ):
            assert mod._client is not None
            mod._client.post = capture_post  # type: ignore[method-assign]
            await mod._upload("ab" * 32, 1000, 2, "ff", 0.0, 0.0)

        assert captured[0]["publicKey"] == fake_public.hex()

        await mod.stop()


# ---------------------------------------------------------------------------
# _get_radio_params
# ---------------------------------------------------------------------------


class TestGetRadioParams:
    def test_returns_zeros_when_radio_not_connected(self):
        with patch("app.fanout.map_upload.radio_runtime") as mock_rt:
            mock_rt.meshcore = None
            params = _get_radio_params()
        assert params == {"freq": 0, "cr": 0, "sf": 0, "bw": 0}

    def test_returns_zeros_on_exception(self):
        mock_rt = MagicMock()
        type(mock_rt).meshcore = PropertyMock(side_effect=Exception("boom"))
        with patch("app.fanout.map_upload.radio_runtime", mock_rt):
            params = _get_radio_params()
        assert params == {"freq": 0, "cr": 0, "sf": 0, "bw": 0}

    def test_passes_freq_and_bw_directly(self):
        """Python lib already returns freq in MHz and bw in kHz — no division needed."""
        mock_rt = MagicMock()
        mock_rt.meshcore.self_info = {
            "radio_freq": 915.0,
            "radio_bw": 125.0,
            "radio_sf": 10,
            "radio_cr": 5,
        }
        with patch("app.fanout.map_upload.radio_runtime", mock_rt):
            params = _get_radio_params()
        assert params["freq"] == 915.0
        assert params["bw"] == 125.0
        assert params["sf"] == 10
        assert params["cr"] == 5


# ---------------------------------------------------------------------------
# Location guard
# ---------------------------------------------------------------------------


class TestLocationGuard:
    @pytest.mark.asyncio
    async def test_no_location_skipped(self):
        """Advert with lat=None and lon=None must not be uploaded."""
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2, lat=None, lon=None),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_lat_none_skipped(self):
        """Advert with only lat=None must not be uploaded."""
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2, lat=None, lon=-0.1),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_lon_none_skipped(self):
        """Advert with only lon=None must not be uploaded."""
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2, lat=51.5, lon=None),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_not_called()

        await mod.stop()

    @pytest.mark.asyncio
    async def test_valid_location_passes(self):
        """Advert with valid lat/lon must proceed to _upload."""
        mod = _make_module()
        await mod.start()

        mock_packet = MagicMock()
        mock_packet.payload = b"\x00" * 101

        with (
            patch("app.fanout.map_upload.parse_packet", return_value=mock_packet),
            patch(
                "app.fanout.map_upload.parse_advertisement",
                return_value=_fake_advert(device_role=2, lat=51.5, lon=-0.1),
            ),
            patch.object(mod, "_upload", new_callable=AsyncMock) as mock_upload,
        ):
            await mod.on_raw(_advert_raw_data())
            mock_upload.assert_called_once()

        await mod.stop()


# ---------------------------------------------------------------------------
# Geofence
# ---------------------------------------------------------------------------

# Shared helpers for radio_runtime patching in geofence tests
_FAKE_PRIVATE = bytes(range(64))
_FAKE_PUBLIC = bytes(range(32))
_FAKE_RADIO_PARAMS = {"freq": 0, "cr": 0, "sf": 0, "bw": 0}


def _mock_radio_runtime_with_location(lat: float, lon: float):
    """Return a context-manager mock for radio_runtime with the given lat/lon."""
    mock_rt = MagicMock()
    mock_rt.meshcore.self_info = {"adv_lat": lat, "adv_lon": lon}
    return patch("app.fanout.map_upload.radio_runtime", mock_rt)


class TestGeofence:
    @pytest.mark.asyncio
    async def test_geofence_disabled_passes_through(self):
        """geofence_enabled=False (default) must not filter anything."""
        mod = _make_module({"dry_run": True, "geofence_enabled": False})
        await mod.start()

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            await mod._upload("ab" * 32, 1000, 2, "aabb", 51.5, -0.1)
            assert ("ab" * 32) in mod._seen

        await mod.stop()

    @pytest.mark.asyncio
    async def test_node_inside_fence_uploaded(self):
        """Node within the configured radius must be uploaded."""
        mod = _make_module(
            {
                "dry_run": True,
                "geofence_enabled": True,
                "geofence_radius_km": 100.0,
            }
        )
        await mod.start()

        with (
            _mock_radio_runtime_with_location(51.5, -0.1),
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            # ~50 km north of the fence center
            await mod._upload("ab" * 32, 1000, 2, "aabb", 51.95, -0.1)
            assert ("ab" * 32) in mod._seen

        await mod.stop()

    @pytest.mark.asyncio
    async def test_node_outside_fence_skipped(self):
        """Node beyond the configured radius must be skipped."""
        mod = _make_module(
            {
                "dry_run": True,
                "geofence_enabled": True,
                "geofence_radius_km": 10.0,
            }
        )
        await mod.start()

        with (
            _mock_radio_runtime_with_location(51.5, -0.1),
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            # ~50 km north — outside the 10 km fence
            await mod._upload("ab" * 32, 1000, 2, "aabb", 51.95, -0.1)
            assert ("ab" * 32) not in mod._seen

        await mod.stop()

    @pytest.mark.asyncio
    async def test_node_at_exact_boundary_passes(self):
        """Node at exactly the fence radius must be allowed (> not >=).

        We compute the haversine distance to the node, then set the radius to
        exactly that value.  The geofence check is ``dist > radius``, so a node
        sitting precisely on the boundary must pass through.
        """
        fence_lat, fence_lon = 1.0, 0.0
        node_lat, node_lon = 1.9, 0.0
        exact_dist = _haversine_km(fence_lat, fence_lon, node_lat, node_lon)

        mod = _make_module(
            {
                "dry_run": True,
                "geofence_enabled": True,
                "geofence_radius_km": exact_dist,  # radius == distance → must pass
            }
        )
        await mod.start()

        with (
            _mock_radio_runtime_with_location(fence_lat, fence_lon),
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            await mod._upload("ab" * 32, 1000, 2, "aabb", node_lat, node_lon)
            assert ("ab" * 32) in mod._seen

        await mod.stop()

    @pytest.mark.asyncio
    async def test_node_just_outside_boundary_skipped(self):
        """Node one metre beyond the fence radius must be filtered.

        Companion to test_node_at_exact_boundary_passes: shrink the radius by a
        tiny epsilon so the same node is now strictly outside the fence.
        """
        fence_lat, fence_lon = 1.0, 0.0
        node_lat, node_lon = 1.9, 0.0
        exact_dist = _haversine_km(fence_lat, fence_lon, node_lat, node_lon)

        mod = _make_module(
            {
                "dry_run": True,
                "geofence_enabled": True,
                "geofence_radius_km": exact_dist - 0.001,  # 1 metre short → must be filtered
            }
        )
        await mod.start()

        with (
            _mock_radio_runtime_with_location(fence_lat, fence_lon),
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            await mod._upload("ab" * 32, 1000, 2, "aabb", node_lat, node_lon)
            assert ("ab" * 32) not in mod._seen

        await mod.stop()

    @pytest.mark.asyncio
    async def test_geofence_skipped_when_lat_lon_zero(self):
        """geofence_enabled=True but radio (0, 0) → upload proceeds (geofence silently skipped)."""
        mod = _make_module(
            {
                "dry_run": True,
                "geofence_enabled": True,
                "geofence_radius_km": 10.0,
            }
        )
        await mod.start()

        # Radio is at (0, 0) — treated as "not configured"; all nodes pass through.
        with (
            _mock_radio_runtime_with_location(0.0, 0.0),
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            # This node is many thousands of km from (0,0) — would be filtered if fence active.
            await mod._upload("ab" * 32, 1000, 2, "aabb", 51.5, -0.1)
            assert ("ab" * 32) in mod._seen

        await mod.stop()

    @pytest.mark.asyncio
    async def test_geofence_skipped_when_radio_unavailable(self):
        """geofence_enabled=True but radio is not connected → upload proceeds."""
        mod = _make_module(
            {
                "dry_run": True,
                "geofence_enabled": True,
                "geofence_radius_km": 10.0,
            }
        )
        await mod.start()

        mock_rt = MagicMock()
        mock_rt.meshcore = None  # radio not connected

        with (
            patch("app.fanout.map_upload.radio_runtime", mock_rt),
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            await mod._upload("ab" * 32, 1000, 2, "aabb", 51.5, -0.1)
            assert ("ab" * 32) in mod._seen

        await mod.stop()

    @pytest.mark.asyncio
    async def test_dry_run_geofence_logs_distance(self):
        """dry_run + geofence_enabled must include the calculated distance in the log line."""
        mod = _make_module(
            {
                "dry_run": True,
                "geofence_enabled": True,
                "geofence_radius_km": 100.0,
            }
        )
        await mod.start()

        with (
            _mock_radio_runtime_with_location(51.5, -0.1),
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            with patch("app.fanout.map_upload.logger") as mock_logger:
                # ~50 km north — inside the fence
                await mod._upload("ab" * 32, 1000, 2, "aabb", 51.95, -0.1)
                mock_logger.info.assert_called_once()
                log_message = mock_logger.info.call_args[0][0] % mock_logger.info.call_args[0][1:]
                assert "geofence:" in log_message
                assert "km from observer" in log_message

        await mod.stop()

    @pytest.mark.asyncio
    async def test_dry_run_no_geofence_no_distance_in_log(self):
        """dry_run without geofence_enabled must not include a distance note in the log."""
        mod = _make_module({"dry_run": True, "geofence_enabled": False})
        await mod.start()

        with (
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            with patch("app.fanout.map_upload.logger") as mock_logger:
                await mod._upload("ab" * 32, 1000, 2, "aabb", 51.5, -0.1)
                mock_logger.info.assert_called_once()
                log_message = mock_logger.info.call_args[0][0] % mock_logger.info.call_args[0][1:]
                assert "geofence:" not in log_message

        await mod.stop()

    @pytest.mark.asyncio
    async def test_dry_run_geofence_no_distance_when_lat_lon_zero(self):
        """dry_run + geofence_enabled but radio (0, 0) → no distance note in log (skipped)."""
        mod = _make_module(
            {
                "dry_run": True,
                "geofence_enabled": True,
                "geofence_radius_km": 100.0,
            }
        )
        await mod.start()

        with (
            _mock_radio_runtime_with_location(0.0, 0.0),
            patch("app.fanout.map_upload.get_private_key", return_value=_FAKE_PRIVATE),
            patch("app.fanout.map_upload.get_public_key", return_value=_FAKE_PUBLIC),
            patch("app.fanout.map_upload._get_radio_params", return_value=_FAKE_RADIO_PARAMS),
        ):
            with patch("app.fanout.map_upload.logger") as mock_logger:
                await mod._upload("ab" * 32, 1000, 2, "aabb", 51.5, -0.1)
                # Upload still happens (seen table updated), but log should not mention geofence distance
                assert ("ab" * 32) in mod._seen
                log_calls = mock_logger.info.call_args_list
                for call in log_calls:
                    msg = call[0][0] % call[0][1:] if call[0][1:] else call[0][0]
                    assert "km from observer" not in msg

        await mod.stop()
