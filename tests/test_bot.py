"""Tests for the bot execution module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.fanout.bot_exec as bot_module
from app.fanout.bot_exec import (
    BOT_MESSAGE_SPACING,
    execute_bot_code,
    process_bot_response,
)


class TestExecuteBotCode:
    """Test bot code execution."""

    def test_valid_code_returning_string(self):
        """Bot code that returns a string works correctly."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return f"Hello, {sender_name}!"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result == "Hello, Alice!"

    def test_valid_code_returning_none(self):
        """Bot code that returns None works correctly."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return None
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_empty_string_response_treated_as_none(self):
        """Bot returning empty/whitespace string is treated as None."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return "   "
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_code_with_syntax_error(self):
        """Bot code with syntax error returns None."""
        code = """
def bot(sender_name:
    return "broken"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_code_without_bot_function(self):
        """Code that doesn't define 'bot' function returns None."""
        code = """
def my_function():
    return "hello"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_bot_not_callable(self):
        """Code where 'bot' is not callable returns None."""
        code = """
bot = "I'm a string, not a function"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_bot_function_raises_exception(self):
        """Bot function that raises exception returns None."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    raise ValueError("oops!")
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_bot_returns_non_string(self):
        """Bot function returning non-string returns None."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return 42
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_empty_code_returns_none(self):
        """Empty bot code returns None."""
        result = execute_bot_code(
            code="",
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_whitespace_only_code_returns_none(self):
        """Whitespace-only bot code returns None."""
        result = execute_bot_code(
            code="   \n\t  ",
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_bot_receives_all_parameters(self):
        """Bot function receives all expected parameters including is_outgoing."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    parts = [
        f"name={sender_name}",
        f"key={sender_key}",
        f"msg={message_text}",
        f"dm={is_dm}",
        f"ch_key={channel_key}",
        f"ch_name={channel_name}",
        f"ts={sender_timestamp}",
        f"path={path}",
        f"outgoing={is_outgoing}",
    ]
    return "|".join(parts)
"""
        result = execute_bot_code(
            code=code,
            sender_name="Bob",
            sender_key="def456",
            message_text="Test",
            is_dm=False,
            channel_key="AABBCCDD",
            channel_name="#test",
            sender_timestamp=12345,
            path="001122",
            is_outgoing=True,
        )
        assert (
            result
            == "name=Bob|key=def456|msg=Test|dm=False|ch_key=AABBCCDD|ch_name=#test|ts=12345|path=001122|outgoing=True"
        )

    def test_is_outgoing_defaults_to_false(self):
        """is_outgoing defaults to False when not specified."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return f"outgoing={is_outgoing}"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result == "outgoing=False"

    def test_is_outgoing_true_passed_correctly(self):
        """is_outgoing=True is passed to bot function."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    if is_outgoing:
        return None
    return "reply"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
            is_outgoing=True,
        )
        assert result is None

    def test_legacy_8_param_bot_still_works(self):
        """Legacy bot with 8 parameters (no is_outgoing) still works."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
    return f"Hello, {sender_name}!"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
            is_outgoing=True,
        )
        assert result == "Hello, Alice!"

    def test_legacy_bot_with_kwargs_receives_is_outgoing(self):
        """Legacy bot using **kwargs receives is_outgoing."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, **kwargs):
    return f"outgoing={kwargs.get('is_outgoing', 'missing')}"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
            is_outgoing=True,
        )
        assert result == "outgoing=True"

    def test_new_10_param_bot_receives_path_bytes_per_hop(self):
        """Bots that declare path_bytes_per_hop receive it positionally."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing, path_bytes_per_hop):
    return f"bytes={path_bytes_per_hop}"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path="aabb",
            path_bytes_per_hop=2,
        )
        assert result == "bytes=2"

    def test_9_param_bot_with_path_bytes_only_receives_it(self):
        """Bots may opt into path_bytes_per_hop without also declaring is_outgoing."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, path_bytes_per_hop):
    return f"bytes={path_bytes_per_hop}"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path="aabb",
            is_outgoing=True,
            path_bytes_per_hop=2,
        )
        assert result == "bytes=2"

    def test_legacy_bot_with_kwargs_receives_path_bytes_per_hop(self):
        """Bots using **kwargs receive the new path_bytes_per_hop field."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, **kwargs):
    return f"bytes={kwargs.get('path_bytes_per_hop', 'missing')}"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path="aabb",
            path_bytes_per_hop=2,
        )
        assert result == "bytes=2"

    def test_pure_kwargs_bot_receives_core_fields_and_optional_extras(self):
        """Pure **kwargs bots are first-class and receive the full payload by keyword."""
        code = """
def bot(**kwargs):
    return (
        f"{kwargs.get('sender_name')}|{kwargs.get('message_text')}|"
        f"{kwargs.get('is_outgoing')}|{kwargs.get('path_bytes_per_hop')}"
    )
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path="aabb",
            is_outgoing=True,
            path_bytes_per_hop=2,
        )
        assert result == "Alice|Hi|True|2"

    def test_channel_message_with_none_sender_key(self):
        """Channel messages correctly pass None for sender_key."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    if sender_key is None and not is_dm:
        return "channel message detected"
    return "unexpected"
"""
        result = execute_bot_code(
            code=code,
            sender_name="Someone",
            sender_key=None,  # Channel messages don't have sender key
            message_text="Test",
            is_dm=False,
            channel_key="AABBCCDD",
            channel_name="#general",
            sender_timestamp=None,
            path=None,
        )
        assert result == "channel message detected"

    def test_bot_returns_list_of_strings(self):
        """Bot function returning list of strings works correctly."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return ["First message", "Second message", "Third message"]
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result == ["First message", "Second message", "Third message"]

    def test_bot_returns_empty_list(self):
        """Bot function returning empty list is treated as None."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return []
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None

    def test_bot_returns_list_with_empty_strings_filtered(self):
        """Bot function returning list filters out empty/whitespace strings."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return ["Valid", "", "  ", "Also valid", None, 42]
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        # Only valid non-empty strings should remain
        assert result == ["Valid", "Also valid"]

    def test_bot_returns_list_all_empty_treated_as_none(self):
        """Bot function returning list of all empty strings is treated as None."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing):
    return ["", "   ", ""]
"""
        result = execute_bot_code(
            code=code,
            sender_name="Alice",
            sender_key="abc123",
            message_text="Hi",
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=None,
            path=None,
        )
        assert result is None


class TestBotCodeValidation:
    """Test bot code syntax validation via fanout router."""

    def test_valid_code_passes(self):
        """Valid Python code passes validation."""
        from app.routers.fanout import _validate_bot_config

        # Should not raise
        _validate_bot_config(
            {
                "code": (
                    "def bot(sender_name, sender_key, message_text, is_dm, channel_key, "
                    "channel_name, sender_timestamp, path):\n    return 'hello'"
                )
            }
        )

    def test_pure_kwargs_code_passes(self):
        """Pure **kwargs bots are valid."""
        from app.routers.fanout import _validate_bot_config

        _validate_bot_config({"code": "def bot(**kwargs):\n    return kwargs.get('message_text')"})

    def test_syntax_error_raises(self):
        """Syntax error in code raises HTTPException."""
        from fastapi import HTTPException

        from app.routers.fanout import _validate_bot_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_bot_config({"code": "def bot(:\n    return 'broken'"})

        assert exc_info.value.status_code == 400
        assert "syntax error" in exc_info.value.detail.lower()

    def test_empty_code_raises(self):
        """Empty code raises HTTPException."""
        from fastapi import HTTPException

        from app.routers.fanout import _validate_bot_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_bot_config({"code": ""})

        assert exc_info.value.status_code == 400
        assert "empty" in exc_info.value.detail.lower()

    def test_missing_code_raises(self):
        """Missing code key raises HTTPException."""
        from fastapi import HTTPException

        from app.routers.fanout import _validate_bot_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_bot_config({})

        assert exc_info.value.status_code == 400

    def test_missing_bot_function_raises(self):
        """Code must define a callable bot() function."""
        from fastapi import HTTPException

        from app.routers.fanout import _validate_bot_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_bot_config({"code": "def helper():\n    return 'hello'"})

        assert exc_info.value.status_code == 400
        assert "callable bot() function" in exc_info.value.detail

    def test_unsupported_signature_raises(self):
        """Unsupported bot signatures are rejected with guidance."""
        from fastapi import HTTPException

        from app.routers.fanout import _validate_bot_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_bot_config(
                {
                    "code": (
                        "def bot(sender_name, sender_key, message_text, is_dm, channel_key, "
                        "channel_name, sender_timestamp, path, *, extra_required):\n"
                        "    return extra_required"
                    )
                }
            )

        assert exc_info.value.status_code == 400
        assert "signature is not supported" in exc_info.value.detail.lower()


class TestBotMessageRateLimiting:
    """Test bot message rate limiting for repeater compatibility."""

    @pytest.fixture(autouse=True)
    def reset_rate_limit_state(self):
        """Reset rate limiting state between tests."""
        bot_module._last_bot_send_time = 0.0
        yield
        bot_module._last_bot_send_time = 0.0

    @pytest.mark.asyncio
    async def test_first_send_does_not_wait(self):
        """First bot send should not wait (no previous send)."""
        with (
            patch("app.fanout.bot_exec.time.monotonic", return_value=100.0),
            patch("app.fanout.bot_exec.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("app.routers.messages.send_direct_message", new_callable=AsyncMock) as mock_send,
            patch("app.websocket.broadcast_event"),
        ):
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            mock_send.return_value = mock_message

            await process_bot_response(
                response="Hello!",
                is_dm=True,
                sender_key="abc123def456" * 4,  # 64 chars
                channel_key=None,
            )

            # Should not have slept (first send, _last_bot_send_time was 0)
            mock_sleep.assert_not_called()
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_rapid_second_send_waits(self):
        """Second send within spacing window should wait."""
        # Previous send was at 100.0, current time is 100.5 (0.5 seconds later)
        # So we need to wait 1.5 more seconds to reach 2.0 second spacing
        bot_module._last_bot_send_time = 100.0

        with (
            patch("app.fanout.bot_exec.time.monotonic", return_value=100.5),
            patch("app.fanout.bot_exec.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("app.routers.messages.send_direct_message", new_callable=AsyncMock) as mock_send,
            patch("app.websocket.broadcast_event"),
        ):
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            mock_send.return_value = mock_message

            await process_bot_response(
                response="Hello again!",
                is_dm=True,
                sender_key="abc123def456" * 4,
                channel_key=None,
            )

            # Should have waited 1.5 seconds (2.0 - 0.5 elapsed)
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            assert abs(wait_time - 1.5) < 0.01

    @pytest.mark.asyncio
    async def test_send_after_spacing_does_not_wait(self):
        """Send after spacing window should not wait."""
        # Simulate a previous send 3 seconds ago (> BOT_MESSAGE_SPACING)
        bot_module._last_bot_send_time = 97.0

        with (
            patch("app.fanout.bot_exec.time.monotonic", return_value=100.0),
            patch("app.fanout.bot_exec.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("app.routers.messages.send_direct_message", new_callable=AsyncMock) as mock_send,
            patch("app.websocket.broadcast_event"),
        ):
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            mock_send.return_value = mock_message

            await process_bot_response(
                response="Hello!",
                is_dm=True,
                sender_key="abc123def456" * 4,
                channel_key=None,
            )

            # Should not have slept (3 seconds > 2 second spacing)
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_timestamp_updated_after_successful_send(self):
        """Last send timestamp should be updated after successful send."""
        with (
            patch("app.fanout.bot_exec.time.monotonic", return_value=150.0),
            patch("app.routers.messages.send_direct_message", new_callable=AsyncMock) as mock_send,
            patch("app.websocket.broadcast_event"),
        ):
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            mock_send.return_value = mock_message

            await process_bot_response(
                response="Hello!",
                is_dm=True,
                sender_key="abc123def456" * 4,
                channel_key=None,
            )

            assert bot_module._last_bot_send_time == 150.0

    @pytest.mark.asyncio
    async def test_timestamp_not_updated_on_failure(self):
        """Last send timestamp should NOT be updated if send fails."""
        from fastapi import HTTPException

        bot_module._last_bot_send_time = 50.0  # Previous timestamp

        with (
            patch("app.fanout.bot_exec.time.monotonic", return_value=100.0),
            patch(
                "app.routers.messages.send_direct_message",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=422, detail="Send failed"),
            ),
        ):
            await process_bot_response(
                response="Hello!",
                is_dm=True,
                sender_key="abc123def456" * 4,
                channel_key=None,
            )

            # Timestamp should remain unchanged
            assert bot_module._last_bot_send_time == 50.0

    @pytest.mark.asyncio
    async def test_timestamp_not_updated_on_no_destination(self):
        """Last send timestamp should NOT be updated if no destination."""
        bot_module._last_bot_send_time = 50.0

        with patch("app.fanout.bot_exec.time.monotonic", return_value=100.0):
            await process_bot_response(
                response="Hello!",
                is_dm=False,  # Not a DM
                sender_key="",
                channel_key=None,  # No channel either
            )

            # Timestamp should remain unchanged
            assert bot_module._last_bot_send_time == 50.0

    @pytest.mark.asyncio
    async def test_concurrent_sends_are_serialized(self):
        """Multiple concurrent sends should be serialized by the lock."""
        send_order = []
        send_times = []

        async def mock_send(*args, **kwargs):
            send_order.append(len(send_order))
            send_times.append(bot_module.time.monotonic())
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            return mock_message

        # Use a real monotonic-like counter for this test
        time_counter = [100.0]

        def mock_monotonic():
            return time_counter[0]

        async def mock_sleep(duration):
            time_counter[0] += duration

        with (
            patch("app.fanout.bot_exec.time.monotonic", side_effect=mock_monotonic),
            patch("app.fanout.bot_exec.asyncio.sleep", side_effect=mock_sleep),
            patch("app.routers.messages.send_direct_message", side_effect=mock_send),
            patch("app.websocket.broadcast_event"),
        ):
            # Launch 3 concurrent sends
            await asyncio.gather(
                process_bot_response("Msg 1", True, "a" * 64, None),
                process_bot_response("Msg 2", True, "b" * 64, None),
                process_bot_response("Msg 3", True, "c" * 64, None),
            )

            # All 3 should have sent
            assert len(send_order) == 3

            # Each send should be at least BOT_MESSAGE_SPACING apart
            # First send at 100, second at 102, third at 104
            assert send_times[1] >= send_times[0] + BOT_MESSAGE_SPACING - 0.01
            assert send_times[2] >= send_times[1] + BOT_MESSAGE_SPACING - 0.01

    @pytest.mark.asyncio
    async def test_channel_message_rate_limited(self):
        """Channel message sends should also be rate limited."""
        bot_module._last_bot_send_time = 99.0  # 1 second ago

        with (
            patch("app.fanout.bot_exec.time.monotonic", return_value=100.0),
            patch("app.fanout.bot_exec.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("app.routers.messages.send_channel_message", new_callable=AsyncMock) as mock_send,
            patch("app.websocket.broadcast_event"),
        ):
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            mock_send.return_value = mock_message

            await process_bot_response(
                response="Channel hello!",
                is_dm=False,
                sender_key="",
                channel_key="AABBCCDD" * 4,
            )

            # Should have waited 1 second (2.0 - 1.0 elapsed)
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            assert abs(wait_time - 1.0) < 0.01
            mock_send.assert_called_once()


class TestBotListResponses:
    """Test bot functionality for list responses."""

    @pytest.fixture(autouse=True)
    def reset_rate_limit_state(self):
        """Reset rate limiting state between tests."""
        bot_module._last_bot_send_time = 0.0
        yield
        bot_module._last_bot_send_time = 0.0

    @pytest.mark.asyncio
    async def test_list_response_sends_multiple_messages(self):
        """List response should send multiple messages in order."""
        sent_messages = []

        async def mock_send(request):
            sent_messages.append(request.text)
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            return mock_message

        with (
            patch("app.fanout.bot_exec.time.monotonic", return_value=100.0),
            patch("app.fanout.bot_exec.asyncio.sleep", new_callable=AsyncMock),
            patch("app.routers.messages.send_direct_message", side_effect=mock_send),
            patch("app.websocket.broadcast_event"),
        ):
            await process_bot_response(
                response=["First", "Second", "Third"],
                is_dm=True,
                sender_key="a" * 64,
                channel_key=None,
            )

            assert sent_messages == ["First", "Second", "Third"]

    @pytest.mark.asyncio
    async def test_list_response_rate_limited_between_messages(self):
        """Each message in a list should be rate limited."""
        sleep_calls = []

        time_counter = [100.0]

        def mock_monotonic():
            return time_counter[0]

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            time_counter[0] += duration

        async def mock_send(request):
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            return mock_message

        with (
            patch("app.fanout.bot_exec.time.monotonic", side_effect=mock_monotonic),
            patch("app.fanout.bot_exec.asyncio.sleep", side_effect=mock_sleep),
            patch("app.routers.messages.send_direct_message", side_effect=mock_send),
            patch("app.websocket.broadcast_event"),
        ):
            await process_bot_response(
                response=["First", "Second", "Third"],
                is_dm=True,
                sender_key="a" * 64,
                channel_key=None,
            )

            # Should have waited between messages (after first send)
            # First message: no wait, Second: wait 2s, Third: wait 2s
            assert len(sleep_calls) == 2
            assert all(abs(w - BOT_MESSAGE_SPACING) < 0.01 for w in sleep_calls)

    @pytest.mark.asyncio
    async def test_string_response_still_works(self):
        """Single string response should still work after list support added."""
        sent_messages = []

        async def mock_send(request):
            sent_messages.append(request.text)
            mock_message = MagicMock()
            mock_message.model_dump.return_value = {}
            return mock_message

        with (
            patch("app.fanout.bot_exec.time.monotonic", return_value=100.0),
            patch("app.fanout.bot_exec.asyncio.sleep", new_callable=AsyncMock),
            patch("app.routers.messages.send_direct_message", side_effect=mock_send),
            patch("app.websocket.broadcast_event"),
        ):
            await process_bot_response(
                response="Just one message",
                is_dm=True,
                sender_key="a" * 64,
                channel_key=None,
            )

            assert sent_messages == ["Just one message"]
