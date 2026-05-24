"""Virtual rooms and companions status endpoints."""

from fastapi import APIRouter

from app.virtual.models import VirtualCompanionsResponse, VirtualRoomsResponse

router = APIRouter(prefix="/virtual", tags=["virtual"])


@router.get("/rooms", response_model=VirtualRoomsResponse)
async def get_virtual_rooms() -> VirtualRoomsResponse:
    """Return status of all configured virtual room servers."""
    from app.virtual.manager import virtual_manager

    rooms = await virtual_manager.room_statuses()
    return VirtualRoomsResponse(rooms=rooms)


@router.get("/companions", response_model=VirtualCompanionsResponse)
async def get_virtual_companions() -> VirtualCompanionsResponse:
    """Return status of all configured virtual TCP companions."""
    from app.virtual.manager import virtual_manager

    companions = virtual_manager.companion_statuses()
    return VirtualCompanionsResponse(companions=companions)
