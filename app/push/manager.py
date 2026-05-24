"""Web Push dispatch manager.

Checks the global push-enabled conversation list (stored in app_settings)
and sends push notifications to ALL registered devices when a matching
incoming message arrives.
"""

import asyncio
import json
import logging
from dataclasses import dataclass

from pywebpush import WebPushException

from app.push.send import send_push
from app.push.vapid import get_vapid_private_key
from app.repository.channels import ChannelRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

_SEND_TIMEOUT = 15  # seconds per push send
_VAPID_CLAIMS = {"sub": "mailto:noreply@meshcore.local"}


def _state_key_for_message(data: dict) -> str:
    """Derive the conversation state key from a message event payload."""
    msg_type = data.get("type", "")
    conversation_key = data.get("conversation_key", "")
    if msg_type == "PRIV":
        return f"contact-{conversation_key}"
    return f"channel-{conversation_key}"


def _build_payload(data: dict) -> str:
    """Build the push notification JSON payload from a message event."""
    msg_type = data.get("type", "")
    text = data.get("text", "")
    sender_name = data.get("sender_name") or ""
    channel_name = data.get("channel_name") or ""

    if msg_type == "PRIV":
        title = f"Message from {sender_name}" if sender_name else "New direct message"
        body = text
    else:
        title = channel_name if channel_name else "Channel message"
        body = text

    conversation_key = data.get("conversation_key", "")
    state_key = _state_key_for_message(data)
    if msg_type == "PRIV":
        url_hash = f"#contact/{conversation_key}"
    else:
        url_hash = f"#channel/{conversation_key}"

    return json.dumps(
        {
            "title": title,
            "body": body,
            # Tag per conversation so different conversations coexist in the
            # notification tray, while repeated messages in the same
            # conversation replace each other.
            "tag": f"meshcore-{state_key}",
            "url_hash": url_hash,
        }
    )


def _subscription_info(sub: dict) -> dict:
    """Build the subscription_info dict that pywebpush expects."""
    return {
        "endpoint": sub["endpoint"],
        "keys": {
            "p256dh": sub["p256dh"],
            "auth": sub["auth"],
        },
    }


@dataclass
class _SendResult:
    sub_id: str
    success: bool = False
    expired: bool = False


class PushManager:
    async def dispatch_message(self, data: dict) -> None:
        """Send push notifications for a message event to all devices."""
        # Don't notify for messages the operator just sent themselves
        if data.get("outgoing"):
            return

        # Check the global conversation list
        state_key = _state_key_for_message(data)
        try:
            push_conversations = await AppSettingsRepository.get_push_conversations()
        except Exception:
            logger.debug("Push dispatch: failed to load push_conversations", exc_info=True)
            return

        if state_key not in push_conversations:
            return

        # Skip muted channels
        if data.get("type") == "CHAN" and data.get("conversation_key"):
            try:
                ch = await ChannelRepository.get_by_key(data["conversation_key"])
                if ch and ch.muted:
                    return
            except Exception:
                logger.debug("Push dispatch: failed to check channel mute state", exc_info=True)

        try:
            subs = await PushSubscriptionRepository.get_all()
        except Exception:
            logger.debug("Push dispatch: failed to load subscriptions", exc_info=True)
            return

        if not subs:
            return

        payload = _build_payload(data)
        vapid_key = get_vapid_private_key()
        if not vapid_key:
            logger.debug("Push dispatch: no VAPID key configured, skipping")
            return

        results = await asyncio.gather(
            *(self._send_one(sub, payload, vapid_key) for sub in subs),
            return_exceptions=True,
        )

        # Batch-update all delivery outcomes in one transaction.
        success_ids: list[str] = []
        failure_ids: list[str] = []
        remove_ids: list[str] = []
        for r in results:
            if isinstance(r, _SendResult):
                if r.expired:
                    remove_ids.append(r.sub_id)
                elif r.success:
                    success_ids.append(r.sub_id)
                else:
                    failure_ids.append(r.sub_id)
        if success_ids or failure_ids or remove_ids:
            try:
                await PushSubscriptionRepository.batch_record_outcomes(
                    success_ids, failure_ids, remove_ids
                )
            except Exception:
                logger.debug("Push dispatch: failed to record outcomes", exc_info=True)

    async def _send_one(self, sub: dict, payload: str, vapid_key: str) -> _SendResult:
        sub_id = sub["id"]
        result = _SendResult(sub_id=sub_id)
        try:
            async with asyncio.timeout(_SEND_TIMEOUT):
                await send_push(
                    subscription_info=_subscription_info(sub),
                    payload=payload,
                    vapid_private_key=vapid_key,
                    vapid_claims=_VAPID_CLAIMS,
                )
            result.success = True
        except WebPushException as e:
            status = getattr(e, "response", None)
            status_code = getattr(status, "status_code", 0) if status else 0
            if status_code in (403, 404, 410):
                logger.info("Push subscription expired (HTTP %d), removing %s", status_code, sub_id)
                result.expired = True
            else:
                logger.warning("Push send failed for %s: %s", sub_id, e)
        except TimeoutError:
            logger.warning("Push send timed out for %s", sub_id)
        except Exception:
            logger.debug("Push send error for %s", sub_id, exc_info=True)
        return result


push_manager = PushManager()
