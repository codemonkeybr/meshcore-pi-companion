"""Web Push subscription management endpoints."""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pywebpush import WebPushException

from app.push.send import send_push
from app.push.vapid import get_vapid_private_key, get_vapid_public_key
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])


# ── Request/response models ─────────────────────────────────────────────


class VapidPublicKeyResponse(BaseModel):
    public_key: str


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1)
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)
    label: str = ""


class PushSubscriptionUpdate(BaseModel):
    label: str | None = None


class PushConversationToggle(BaseModel):
    key: str = Field(min_length=1)


# ─��� Endpoints ────────────────────────────────────────────────────────────


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
async def vapid_public_key() -> VapidPublicKeyResponse:
    """Return the VAPID public key for browser PushManager.subscribe()."""
    key = get_vapid_public_key()
    if not key:
        raise HTTPException(status_code=423, detail="VAPID keys not initialized")
    return VapidPublicKeyResponse(public_key=key)


@router.post("/subscribe")
async def subscribe(body: PushSubscribeRequest) -> dict:
    """Register or update a push subscription (device). Upserts by endpoint."""
    sub = await PushSubscriptionRepository.create(
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        label=body.label,
    )
    return sub


@router.get("/subscriptions")
async def list_subscriptions() -> list[dict]:
    """List all push subscriptions (devices)."""
    return await PushSubscriptionRepository.get_all()


@router.patch("/subscriptions/{subscription_id}")
async def update_subscription(subscription_id: str, body: PushSubscriptionUpdate) -> dict:
    """Update a subscription's label."""
    existing = await PushSubscriptionRepository.get(subscription_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Subscription not found")

    updates = {}
    if body.label is not None:
        updates["label"] = body.label

    result = await PushSubscriptionRepository.update(subscription_id, **updates)
    return result or existing


@router.delete("/subscriptions/{subscription_id}")
async def unsubscribe(subscription_id: str) -> dict:
    """Delete a push subscription (device)."""
    deleted = await PushSubscriptionRepository.delete(subscription_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"deleted": True}


@router.post("/subscriptions/{subscription_id}/test")
async def test_push(subscription_id: str) -> dict:
    """Send a test notification to a subscription."""
    sub = await PushSubscriptionRepository.get(subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    vapid_key = get_vapid_private_key()
    if not vapid_key:
        raise HTTPException(status_code=423, detail="VAPID keys not initialized")

    payload = json.dumps(
        {
            "title": "RemoteTerm Test",
            "body": "Push notifications are working!",
            "tag": "meshcore-test",
            "url_hash": "",
        }
    )

    try:
        async with asyncio.timeout(15):
            await send_push(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                payload=payload,
                vapid_private_key=vapid_key,
                vapid_claims={"sub": "mailto:noreply@meshcore.local"},
            )
        return {"status": "sent"}
    except TimeoutError:
        raise HTTPException(status_code=408, detail="Push delivery timed out") from None
    except WebPushException as e:
        status_code = getattr(getattr(e, "response", None), "status_code", 0)
        if status_code in (403, 404, 410):
            logger.info(
                "Test push: subscription stale (HTTP %d), removing %s",
                status_code,
                subscription_id,
            )
            await PushSubscriptionRepository.delete(subscription_id)
            raise HTTPException(
                status_code=410,
                detail="Subscription is stale (VAPID key mismatch or expired). "
                "Re-enable push from a conversation header.",
            ) from None
        logger.warning("Test push failed: %s", e)
        raise HTTPException(status_code=422, detail=f"Push delivery failed: {e}") from None
    except Exception as e:
        logger.warning("Test push failed: %s", e)
        raise HTTPException(status_code=422, detail=f"Push delivery failed: {e}") from None


# ── Global push conversation management ──────────────────────────────────


@router.get("/conversations")
async def get_push_conversations() -> list[str]:
    """Return the global list of push-enabled conversation state keys."""
    return await AppSettingsRepository.get_push_conversations()


@router.post("/conversations/toggle")
async def toggle_push_conversation(body: PushConversationToggle) -> list[str]:
    """Add or remove a conversation from the global push list."""
    return await AppSettingsRepository.toggle_push_conversation(body.key)
