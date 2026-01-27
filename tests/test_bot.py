"""Tests for the bot execution module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.bot as bot_module
from app.bot import (
    BOT_MESSAGE_SPACING,
    _bot_semaphore,
    execute_bot_code,
    process_bot_response,
    run_bot_for_message,
)


class TestExecuteBotCode:
    """Test bot code execution."""

    def test_valid_code_returning_string(self):
        """Bot code that returns a string works correctly."""
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
        )
        assert result == "Hello, Alice!"

    def test_valid_code_returning_none(self):
        """Bot code that returns None works correctly."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
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
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
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
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
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
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
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
        """Bot function receives all expected parameters."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
    # Verify all params are accessible
    parts = [
        f"name={sender_name}",
        f"key={sender_key}",
        f"msg={message_text}",
        f"dm={is_dm}",
        f"ch_key={channel_key}",
        f"ch_name={channel_name}",
        f"ts={sender_timestamp}",
        f"path={path}",
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
        )
        assert (
            result
            == "name=Bob|key=def456|msg=Test|dm=False|ch_key=AABBCCDD|ch_name=#test|ts=12345|path=001122"
        )

    def test_channel_message_with_none_sender_key(self):
        """Channel messages correctly pass None for sender_key."""
        code = """
def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
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


class TestRunBotForMessage:
    """Test the main bot entry point."""

    @pytest.fixture(autouse=True)
    def reset_semaphore(self):
        """Reset semaphore state between tests."""
        # Ensure semaphore is fully released
        while _bot_semaphore.locked():
            _bot_semaphore.release()
        yield

    @pytest.mark.asyncio
    async def test_skips_outgoing_messages(self):
        """Bot is not triggered for outgoing messages."""
        with patch("app.repository.AppSettingsRepository") as mock_repo:
            await run_bot_for_message(
                sender_name="Me",
                sender_key="abc123",
                message_text="Hello",
                is_dm=True,
                channel_key=None,
                is_outgoing=True,
            )

            # Should not even check settings
            mock_repo.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_bot_disabled(self):
        """Bot is not triggered when disabled in settings."""
        with patch("app.repository.AppSettingsRepository") as mock_repo:
            mock_settings = MagicMock()
            mock_settings.bot_enabled = False
            mock_settings.bot_code = "def bot(): pass"
            mock_repo.get = AsyncMock(return_value=mock_settings)

            with patch("app.bot.execute_bot_code") as mock_exec:
                await run_bot_for_message(
                    sender_name="Alice",
                    sender_key="abc123",
                    message_text="Hello",
                    is_dm=True,
                    channel_key=None,
                )

                mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_bot_code_empty(self):
        """Bot is not triggered when code is empty."""
        with patch("app.repository.AppSettingsRepository") as mock_repo:
            mock_settings = MagicMock()
            mock_settings.bot_enabled = True
            mock_settings.bot_code = ""
            mock_repo.get = AsyncMock(return_value=mock_settings)

            with patch("app.bot.execute_bot_code") as mock_exec:
                await run_bot_for_message(
                    sender_name="Alice",
                    sender_key="abc123",
                    message_text="Hello",
                    is_dm=True,
                    channel_key=None,
                )

                mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_rechecks_settings_after_sleep(self):
        """Settings are re-checked after 2 second sleep."""
        with patch("app.repository.AppSettingsRepository") as mock_repo:
            # First call: bot enabled
            # Second call (after sleep): bot disabled
            mock_settings_enabled = MagicMock()
            mock_settings_enabled.bot_enabled = True
            mock_settings_enabled.bot_code = "def bot(): return 'hi'"

            mock_settings_disabled = MagicMock()
            mock_settings_disabled.bot_enabled = False
            mock_settings_disabled.bot_code = "def bot(): return 'hi'"

            mock_repo.get = AsyncMock(side_effect=[mock_settings_enabled, mock_settings_disabled])

            with (
                patch("app.bot.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
                patch("app.bot.execute_bot_code") as mock_exec,
            ):
                await run_bot_for_message(
                    sender_name="Alice",
                    sender_key="abc123",
                    message_text="Hello",
                    is_dm=True,
                    channel_key=None,
                )

                # Should have slept
                mock_sleep.assert_called_once_with(2)

                # Should NOT have executed bot (disabled after sleep)
                mock_exec.assert_not_called()


class TestBotCodeValidation:
    """Test bot code syntax validation on save."""

    def test_valid_code_passes(self):
        """Valid Python code passes validation."""
        from app.routers.settings import validate_bot_code

        # Should not raise
        validate_bot_code("def bot(): return 'hello'")

    def test_syntax_error_raises(self):
        """Syntax error in code raises HTTPException."""
        from fastapi import HTTPException

        from app.routers.settings import validate_bot_code

        with pytest.raises(HTTPException) as exc_info:
            validate_bot_code("def bot(:\n    return 'broken'")

        assert exc_info.value.status_code == 400
        assert "syntax error" in exc_info.value.detail.lower()

    def test_empty_code_passes(self):
        """Empty code passes validation (disables bot)."""
        from app.routers.settings import validate_bot_code

        # Should not raise
        validate_bot_code("")
        validate_bot_code("   ")


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
            patch("app.bot.time.monotonic", return_value=100.0),
            patch("app.bot.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
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
            patch("app.bot.time.monotonic", return_value=100.5),
            patch("app.bot.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
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
            patch("app.bot.time.monotonic", return_value=100.0),
            patch("app.bot.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
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
            patch("app.bot.time.monotonic", return_value=150.0),
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
            patch("app.bot.time.monotonic", return_value=100.0),
            patch(
                "app.routers.messages.send_direct_message",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=500, detail="Send failed"),
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

        with patch("app.bot.time.monotonic", return_value=100.0):
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
            patch("app.bot.time.monotonic", side_effect=mock_monotonic),
            patch("app.bot.asyncio.sleep", side_effect=mock_sleep),
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
            patch("app.bot.time.monotonic", return_value=100.0),
            patch("app.bot.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
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
