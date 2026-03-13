"""Shared dependencies for FastAPI routers."""

from fastapi import HTTPException

from app.radio import radio_manager
from app.radio_backend import RadioBackend


def require_connected() -> RadioBackend:
    """Dependency that ensures radio is connected and returns the backend.

    Raises HTTPException 503 if radio is not connected.
    """
    if getattr(radio_manager, "is_setup_in_progress", False) is True:
        raise HTTPException(status_code=503, detail="Radio is initializing")
    if not radio_manager.is_connected or radio_manager.backend is None:
        raise HTTPException(status_code=503, detail="Radio not connected")
    return radio_manager.backend
