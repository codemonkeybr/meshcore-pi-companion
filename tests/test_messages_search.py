"""Tests for message search, around, and forward pagination."""

import pytest

from app.radio import radio_manager
from app.repository import MessageRepository

CHAN_KEY = "ABC123DEF456ABC123DEF456ABC12345"
DM_KEY = "aa" * 32
OTHER_CHAN_KEY = "FF" * 16


class TestMessageSearch:
    """Tests for the q (search) parameter on get_all."""

    @pytest.mark.asyncio
    async def test_basic_search(self, test_db):
        await MessageRepository.create(
            msg_type="CHAN",
            text="hello world",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="goodbye moon",
            conversation_key=CHAN_KEY,
            sender_timestamp=101,
            received_at=101,
        )

        results = await MessageRepository.get_all(q="hello")
        assert len(results) == 1
        assert results[0].text == "hello world"

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self, test_db):
        await MessageRepository.create(
            msg_type="CHAN",
            text="Hello World",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )

        results = await MessageRepository.get_all(q="hello")
        assert len(results) == 1

        results = await MessageRepository.get_all(q="HELLO")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_pagination(self, test_db):
        for i in range(5):
            await MessageRepository.create(
                msg_type="CHAN",
                text=f"test message {i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )

        results = await MessageRepository.get_all(q="test message", limit=2)
        assert len(results) == 2

        results = await MessageRepository.get_all(q="test message", limit=2, offset=2)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_within_conversation(self, test_db):
        await MessageRepository.create(
            msg_type="CHAN",
            text="hello from channel",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="hello from other",
            conversation_key=OTHER_CHAN_KEY,
            sender_timestamp=101,
            received_at=101,
        )

        results = await MessageRepository.get_all(q="hello", conversation_key=CHAN_KEY)
        assert len(results) == 1
        assert results[0].text == "hello from channel"

    @pytest.mark.asyncio
    async def test_search_no_results(self, test_db):
        await MessageRepository.create(
            msg_type="CHAN",
            text="hello world",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )

        results = await MessageRepository.get_all(q="nonexistent")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_across_types(self, test_db):
        await MessageRepository.create(
            msg_type="CHAN",
            text="search target in chan",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="search target in dm",
            conversation_key=DM_KEY,
            sender_timestamp=101,
            received_at=101,
        )

        results = await MessageRepository.get_all(q="search target")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_returns_sender_name(self, test_db):
        await MessageRepository.create(
            msg_type="CHAN",
            text="Alice: hello world",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
            sender_name="Alice",
        )

        results = await MessageRepository.get_all(q="hello")
        assert len(results) == 1
        assert results[0].sender_name == "Alice"


class TestMessagesAround:
    """Tests for get_around()."""

    @pytest.mark.asyncio
    async def test_returns_context(self, test_db):
        ids = []
        for i in range(10):
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
            ids.append(msg_id)

        # Get around the middle message (index 5)
        messages, has_older, has_newer = await MessageRepository.get_around(
            message_id=ids[5],
            msg_type="CHAN",
            conversation_key=CHAN_KEY,
        )

        assert len(messages) == 10
        assert not has_older  # Only 5 before, context_size defaults to 100
        assert not has_newer  # Only 4 after

    @pytest.mark.asyncio
    async def test_has_older_has_newer(self, test_db):
        ids = []
        for i in range(20):
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
            ids.append(msg_id)

        messages, has_older, has_newer = await MessageRepository.get_around(
            message_id=ids[10],
            msg_type="CHAN",
            conversation_key=CHAN_KEY,
            context_size=3,
        )

        # 3 before + target + 3 after = 7
        assert len(messages) == 7
        assert has_older  # 10 messages before, context_size=3
        assert has_newer  # 9 messages after, context_size=3

    @pytest.mark.asyncio
    async def test_nonexistent_message(self, test_db):
        messages, has_older, has_newer = await MessageRepository.get_around(
            message_id=99999,
        )
        assert messages == []
        assert not has_older
        assert not has_newer

    @pytest.mark.asyncio
    async def test_conversation_key_filter(self, test_db):
        # Create messages in two channels
        for i in range(5):
            await MessageRepository.create(
                msg_type="CHAN",
                text=f"chan1 msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
        for i in range(5):
            await MessageRepository.create(
                msg_type="CHAN",
                text=f"chan2 msg{i}",
                conversation_key=OTHER_CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )

        # Get the target from channel 1
        all_chan1 = await MessageRepository.get_all(conversation_key=CHAN_KEY)
        target_id = all_chan1[2].id

        messages, _, _ = await MessageRepository.get_around(
            message_id=target_id,
            msg_type="CHAN",
            conversation_key=CHAN_KEY,
        )

        # All returned messages should be from channel 1
        for msg in messages:
            assert msg.conversation_key == CHAN_KEY

    @pytest.mark.asyncio
    async def test_context_size(self, test_db):
        ids = []
        for i in range(10):
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
            ids.append(msg_id)

        messages, has_older, has_newer = await MessageRepository.get_around(
            message_id=ids[5],
            msg_type="CHAN",
            conversation_key=CHAN_KEY,
            context_size=2,
        )

        # 2 before + target + 2 after = 5
        assert len(messages) == 5
        assert has_older  # 5 before, context=2
        assert has_newer  # 4 after, context=2

    @pytest.mark.asyncio
    async def test_target_not_in_filtered_conversation_returns_empty(self, test_db):
        target_id = await MessageRepository.create(
            msg_type="CHAN",
            text="target in channel 1",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="message in channel 2",
            conversation_key=OTHER_CHAN_KEY,
            sender_timestamp=101,
            received_at=101,
        )

        messages, has_older, has_newer = await MessageRepository.get_around(
            message_id=target_id,
            msg_type="CHAN",
            conversation_key=OTHER_CHAN_KEY,
        )

        assert messages == []
        assert not has_older
        assert not has_newer


class TestForwardPagination:
    """Tests for the after/after_id forward cursor on get_all."""

    @pytest.mark.asyncio
    async def test_forward_pagination(self, test_db):
        ids = []
        for i in range(5):
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
            ids.append(msg_id)

        # Get first page (DESC order)
        page1 = await MessageRepository.get_all(
            msg_type="CHAN",
            conversation_key=CHAN_KEY,
            limit=3,
        )
        assert len(page1) == 3
        # Page 1 is DESC: msg4, msg3, msg2

        # Get forward from msg2 (oldest in page1)
        newest = page1[0]  # msg4
        forward = await MessageRepository.get_all(
            msg_type="CHAN",
            conversation_key=CHAN_KEY,
            after=newest.received_at,
            after_id=newest.id,
            limit=10,
        )
        # Nothing newer than msg4
        assert len(forward) == 0

    @pytest.mark.asyncio
    async def test_forward_pagination_returns_asc(self, test_db):
        ids = []
        for i in range(5):
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
            ids.append(msg_id)

        # Forward from the first message
        forward = await MessageRepository.get_all(
            msg_type="CHAN",
            conversation_key=CHAN_KEY,
            after=100,
            after_id=ids[0],
            limit=10,
        )
        assert len(forward) == 4  # msg1, msg2, msg3, msg4
        # Should be ASC order
        for i in range(len(forward) - 1):
            assert forward[i].received_at <= forward[i + 1].received_at

    @pytest.mark.asyncio
    async def test_forward_with_conversation_key(self, test_db):
        for i in range(3):
            await MessageRepository.create(
                msg_type="CHAN",
                text=f"chan1 msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
        for i in range(3):
            await MessageRepository.create(
                msg_type="CHAN",
                text=f"chan2 msg{i}",
                conversation_key=OTHER_CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )

        chan1_msgs = await MessageRepository.get_all(conversation_key=CHAN_KEY)
        oldest = chan1_msgs[-1]

        forward = await MessageRepository.get_all(
            conversation_key=CHAN_KEY,
            after=oldest.received_at,
            after_id=oldest.id,
            limit=10,
        )
        for msg in forward:
            assert msg.conversation_key == CHAN_KEY


class TestSearchLikeEscaping:
    """Tests for LIKE wildcard escaping in search."""

    @pytest.mark.asyncio
    async def test_percent_in_query_is_literal(self, test_db):
        await MessageRepository.create(
            msg_type="CHAN",
            text="100% done",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="100 items done",
            conversation_key=CHAN_KEY,
            sender_timestamp=101,
            received_at=101,
        )

        results = await MessageRepository.get_all(q="100%")
        assert len(results) == 1
        assert results[0].text == "100% done"

    @pytest.mark.asyncio
    async def test_underscore_in_query_is_literal(self, test_db):
        await MessageRepository.create(
            msg_type="CHAN",
            text="hello_world",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="helloXworld",
            conversation_key=CHAN_KEY,
            sender_timestamp=101,
            received_at=101,
        )

        results = await MessageRepository.get_all(q="hello_world")
        assert len(results) == 1
        assert results[0].text == "hello_world"


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._backend
    prev_lock = radio_manager._operation_lock
    yield
    radio_manager._backend = prev
    radio_manager._operation_lock = prev_lock


class TestMessagesAroundEndpoint:
    """HTTP-level tests for GET /api/messages/around/{id}."""

    @pytest.mark.asyncio
    async def test_around_returns_context(self, test_db, client):
        ids = []
        for i in range(10):
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
            ids.append(msg_id)

        response = await client.get(
            f"/api/messages/around/{ids[5]}",
            params={"type": "CHAN", "conversation_key": CHAN_KEY},
        )

        assert response.status_code == 200
        body = response.json()
        assert "messages" in body
        assert "has_older" in body
        assert "has_newer" in body
        assert len(body["messages"]) == 10
        assert not body["has_older"]
        assert not body["has_newer"]

    @pytest.mark.asyncio
    async def test_around_nonexistent_returns_empty(self, test_db, client):
        response = await client.get("/api/messages/around/99999")

        assert response.status_code == 200
        body = response.json()
        assert body["messages"] == []
        assert not body["has_older"]
        assert not body["has_newer"]

    @pytest.mark.asyncio
    async def test_around_respects_context_param(self, test_db, client):
        ids = []
        for i in range(20):
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
            ids.append(msg_id)

        response = await client.get(
            f"/api/messages/around/{ids[10]}",
            params={"type": "CHAN", "conversation_key": CHAN_KEY, "context": 3},
        )

        assert response.status_code == 200
        body = response.json()
        # 3 before + target + 3 after = 7
        assert len(body["messages"]) == 7
        assert body["has_older"]
        assert body["has_newer"]

    @pytest.mark.asyncio
    async def test_around_message_fields_serialized(self, test_db, client):
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="Alice: test message",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
            sender_name="Alice",
        )

        response = await client.get(f"/api/messages/around/{msg_id}")
        assert response.status_code == 200
        body = response.json()
        assert len(body["messages"]) == 1
        msg = body["messages"][0]
        assert msg["id"] == msg_id
        assert msg["type"] == "CHAN"
        assert msg["text"] == "Alice: test message"
        assert msg["sender_name"] == "Alice"


class TestSearchEndpoint:
    """HTTP-level tests for GET /api/messages?q=..."""

    @pytest.mark.asyncio
    async def test_search_via_endpoint(self, test_db, client):
        await MessageRepository.create(
            msg_type="CHAN",
            text="hello world",
            conversation_key=CHAN_KEY,
            sender_timestamp=100,
            received_at=100,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="goodbye moon",
            conversation_key=CHAN_KEY,
            sender_timestamp=101,
            received_at=101,
        )

        response = await client.get("/api/messages", params={"q": "hello"})
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_forward_pagination_via_endpoint(self, test_db, client):
        ids = []
        for i in range(5):
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"msg{i}",
                conversation_key=CHAN_KEY,
                sender_timestamp=100 + i,
                received_at=100 + i,
            )
            ids.append(msg_id)

        response = await client.get(
            "/api/messages",
            params={
                "type": "CHAN",
                "conversation_key": CHAN_KEY,
                "after": 100,
                "after_id": ids[0],
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 4
        # Forward results should be ASC
        for i in range(len(results) - 1):
            assert results[i]["received_at"] <= results[i + 1]["received_at"]
