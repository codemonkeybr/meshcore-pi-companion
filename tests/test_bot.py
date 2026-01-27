"""Tests for the bot execution module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot import (
    _bot_semaphore,
    execute_bot_code,
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
