"""WebSocket router for real-time updates."""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.routers.health import build_health_data
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time updates.

    Only sends health status on initial connect. Contacts and channels
    are fetched via REST endpoints for faster parallel loading.
    """
    await ws_manager.connect(websocket)

    # Send initial health status
    try:
        health_data = await build_health_data(
            radio_manager.is_connected, radio_manager.connection_info
        )
        await ws_manager.send_personal(websocket, "health", health_data)

    except Exception as e:
        logger.error("Error sending initial state: %s", e)

    # Keep connection alive and handle incoming messages
    try:
        while True:
            # We don't expect messages from client, but need to keep connection open
            # and handle pings/pongs
            data = await websocket.receive_text()
            # Client can send "ping" to keep alive
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.debug("WebSocket error: %s", e)
        await ws_manager.disconnect(websocket)
