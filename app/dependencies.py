"""Shared dependencies for FastAPI routers."""

from app.services.radio_runtime import radio_runtime as radio_manager


def require_connected():
    """Dependency that ensures radio is connected and returns meshcore instance."""
    return radio_manager.require_connected()
