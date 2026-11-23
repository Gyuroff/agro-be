"""Настройки на текущото устройство и конфигурация за приложението (docs/api.md §1)."""

from sqlalchemy.orm import Session

from app.core import clock
from app.models.company import CompanySettings
from app.models.identity import Device, PushProvider


class DeviceService:
    @staticmethod
    def mark_biometric_enrolled(session: Session, *, device: Device) -> None:
        device.biometric_enrolled_at = clock.now()
        session.flush()

    @staticmethod
    def set_push_token(session: Session, *, device: Device, provider: PushProvider, token: str) -> None:
        """Празен токен изчиства регистрацията (провайдър `none`)."""
        token = token.strip()
        if token:
            device.push_provider = provider.value
            device.push_token = token
        else:
            device.push_provider = PushProvider.NONE.value
            device.push_token = None
        device.push_token_updated_at = clock.now()
        session.flush()


def get_app_config(session: Session) -> CompanySettings:
    return session.get_one(CompanySettings, 1)
