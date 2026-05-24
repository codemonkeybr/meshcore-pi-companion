"""Pydantic models for virtual rooms and TCP companions."""

from __future__ import annotations

from pydantic import BaseModel


class RoomConfig(BaseModel):
    name: str
    admin_password: str
    guest_password: str
    identity_key: str = ""
    max_posts: int = 32
    latitude: float = 0.0
    longitude: float = 0.0


class CompanionConfig(BaseModel):
    name: str
    tcp_port: int
    bind_address: str = "0.0.0.0"
    tcp_timeout: int = 28800
    identity_key: str = ""


class RoomStatus(BaseModel):
    name: str
    public_key_prefix: str
    client_count: int
    message_count: int
    running: bool


class CompanionStatus(BaseModel):
    name: str
    public_key_prefix: str
    tcp_port: int
    bind_address: str
    connected: bool
    client_address: str | None = None


class VirtualRoomsResponse(BaseModel):
    rooms: list[RoomStatus]


class VirtualCompanionsResponse(BaseModel):
    companions: list[CompanionStatus]
