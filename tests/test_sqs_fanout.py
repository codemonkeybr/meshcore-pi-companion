"""Tests for the SQS fanout module helper functions.

Covers region inference from queue URLs, FIFO deduplication ID fallback chains,
and message group ID construction — the non-trivial logic in app/fanout/sqs.py.
"""

import hashlib

from app.fanout.sqs import (
    _build_message_deduplication_id,
    _build_message_group_id,
    _infer_region_from_queue_url,
    _is_fifo_queue,
)


class TestInferRegionFromQueueUrl:
    """URL parsing for AWS region extraction."""

    def test_standard_us_east_1(self):
        url = "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue"
        assert _infer_region_from_queue_url(url) == "us-east-1"

    def test_standard_eu_west_2(self):
        url = "https://sqs.eu-west-2.amazonaws.com/123456789012/my-queue"
        assert _infer_region_from_queue_url(url) == "eu-west-2"

    def test_china_region(self):
        url = "https://sqs.cn-north-1.amazonaws.com.cn/123456789012/my-queue"
        assert _infer_region_from_queue_url(url) == "cn-north-1"

    def test_non_sqs_hostname_returns_none(self):
        url = "https://s3.us-east-1.amazonaws.com/bucket/key"
        assert _infer_region_from_queue_url(url) is None

    def test_localstack_endpoint_returns_none(self):
        url = "http://localhost:4566/000000000000/my-queue"
        assert _infer_region_from_queue_url(url) is None

    def test_empty_url_returns_none(self):
        assert _infer_region_from_queue_url("") is None

    def test_non_amazonaws_domain_returns_none(self):
        url = "https://sqs.us-east-1.example.com/123/queue"
        assert _infer_region_from_queue_url(url) is None

    def test_fifo_queue_url_still_parses_region(self):
        url = "https://sqs.ap-southeast-1.amazonaws.com/123456789012/my-queue.fifo"
        assert _infer_region_from_queue_url(url) == "ap-southeast-1"


class TestIsFifoQueue:
    def test_fifo_suffix(self):
        assert _is_fifo_queue("https://sqs.us-east-1.amazonaws.com/123/queue.fifo") is True

    def test_standard_queue(self):
        assert _is_fifo_queue("https://sqs.us-east-1.amazonaws.com/123/queue") is False

    def test_trailing_slash_stripped(self):
        assert _is_fifo_queue("https://sqs.us-east-1.amazonaws.com/123/queue.fifo/") is True


class TestBuildMessageGroupId:
    """FIFO message group ID selection."""

    def test_message_event_with_conversation_key(self):
        data = {"conversation_key": "abc123", "text": "hello"}
        assert _build_message_group_id(data, event_type="message") == "message-abc123"

    def test_message_event_without_conversation_key_falls_back(self):
        data = {"text": "hello"}
        assert _build_message_group_id(data, event_type="message") == "message-default"

    def test_raw_packet_event_always_returns_raw_packets(self):
        data = {"id": 1, "payload": "deadbeef"}
        assert _build_message_group_id(data, event_type="raw_packet") == "raw-packets"

    def test_message_event_with_empty_conversation_key_falls_back(self):
        data = {"conversation_key": "  ", "text": "hello"}
        assert _build_message_group_id(data, event_type="message") == "message-default"


class TestBuildMessageDeduplicationId:
    """FIFO deduplication ID fallback chain."""

    def test_message_with_int_id(self):
        data = {"id": 42}
        result = _build_message_deduplication_id(data, event_type="message", body="{}")
        assert result == "message-42"

    def test_message_with_string_id_falls_back_to_hash(self):
        body = '{"event_type":"message","data":{"id":"not-an-int"}}'
        data = {"id": "not-an-int"}
        result = _build_message_deduplication_id(data, event_type="message", body=body)
        assert result == hashlib.sha256(body.encode()).hexdigest()

    def test_message_without_id_falls_back_to_hash(self):
        body = '{"event_type":"message","data":{}}'
        data = {}
        result = _build_message_deduplication_id(data, event_type="message", body=body)
        assert result == hashlib.sha256(body.encode()).hexdigest()

    def test_raw_with_observation_id(self):
        data = {"observation_id": "obs-123", "id": 7}
        result = _build_message_deduplication_id(data, event_type="raw_packet", body="{}")
        assert result == "raw-obs-123"

    def test_raw_with_empty_observation_id_falls_to_packet_id(self):
        data = {"observation_id": "  ", "id": 7}
        result = _build_message_deduplication_id(data, event_type="raw_packet", body="{}")
        assert result == "raw-7"

    def test_raw_with_no_observation_id_uses_packet_id(self):
        data = {"id": 99}
        result = _build_message_deduplication_id(data, event_type="raw_packet", body="{}")
        assert result == "raw-99"

    def test_raw_with_no_ids_falls_back_to_hash(self):
        body = '{"event_type":"raw_packet","data":{}}'
        data = {}
        result = _build_message_deduplication_id(data, event_type="raw_packet", body=body)
        assert result == hashlib.sha256(body.encode()).hexdigest()

    def test_raw_with_non_string_observation_id_falls_to_packet_id(self):
        data = {"observation_id": 123, "id": 5}
        result = _build_message_deduplication_id(data, event_type="raw_packet", body="{}")
        assert result == "raw-5"
