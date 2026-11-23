"""DTO за /me (docs/api.md §1)."""

from typing import Literal

from pydantic import BaseModel


class PushTokenRequest(BaseModel):
    provider: Literal["fcm", "hms"]
    token: str  # празен низ = изчистване


class ConfigDTO(BaseModel):
    app_lock_background_minutes: int
    biometric_max_attempts: int
    company_short_name: str
