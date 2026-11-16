"""DTO за /auth (docs/api.md §1)."""

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    installation_id: str = Field(min_length=1)
    platform: Literal["ios", "android"]
    device_model: str | None = None
    os_version: str | None = None
    app_version: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class UserDTO(BaseModel):
    id: int
    full_name: str
    short_name: str
    email: str


class DeviceDTO(BaseModel):
    id: int
    biometric_enrolled: bool


class LoginResponse(BaseModel):
    access_token: str
    access_expires_in: int
    refresh_token: str
    user: UserDTO
    device: DeviceDTO


class RefreshResponse(BaseModel):
    access_token: str
    access_expires_in: int
    refresh_token: str
