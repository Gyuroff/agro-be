"""Админ: потребители, устройства, фирма, номерация (docs/data-model.md §14)."""

from typing import Any

import anyio
from sqladmin import action
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from wtforms import Form, PasswordField
from wtforms.validators import Length

from app.admin.base import AuditedModelView
from app.core import clock
from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models.company import CompanySettings, NumberSequence
from app.models.identity import Actor, ActorKind, Device, RefreshToken, User
from app.models.invoicing import Invoice

MIN_PASSWORD_LENGTH = 8


class UserAdmin(AuditedModelView, model=User):
    name = "Потребител"
    name_plural = "Потребители"
    category = "Хора и достъп"
    icon = "fa-solid fa-user"

    column_list = [
        User.email,
        User.full_name,
        User.is_active,
        User.is_admin,
        User.push_enabled,
        User.last_login_at,
    ]
    column_searchable_list = [User.email, User.full_name]
    column_labels = {
        "email": "Имейл",
        "full_name": "Име",
        "short_name": "Кратко име",
        "phone": "Телефон",
        "is_active": "Активен",
        "is_admin": "Админ",
        "push_enabled": "Пуш известия",
        "last_login_at": "Последен вход",
        "new_password": "Парола",
    }
    column_details_list = [
        *(c.key for c in User.__table__.columns if c.key != "password_hash"),
    ]
    form_excluded_columns = [
        *AuditedModelView.form_excluded_columns,
        "password_hash",
        "failed_login_count",
        "locked_until",
        "last_login_at",
    ]

    async def scaffold_form(self, rules: list[str] | None = None) -> type[Form]:
        form_class = await super().scaffold_form(rules)
        form_class.new_password = PasswordField(  # pyright: ignore[reportAttributeAccessIssue]
            "Парола",
            validators=[Length(max=200)],
            description="При създаване е задължителна; при редакция — само за смяна.",
        )
        return form_class

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        password = data.pop("new_password", None) or ""
        if is_created and not password:
            raise ValueError("Паролата е задължителна при създаване.")
        if password:
            if len(password) < MIN_PASSWORD_LENGTH:
                raise ValueError(f"Паролата трябва да е поне {MIN_PASSWORD_LENGTH} знака.")
            model.password_hash = hash_password(password)

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        if is_created:
            await anyio.to_thread.run_sync(_ensure_actor, model.id, model.short_name)


def _ensure_actor(user_id: int, short_name: str) -> None:
    """Ред в `actors` за новия потребител (kind=user, code=`user:<id>`); `actors` не се одитира."""
    with get_sessionmaker()() as session:
        code = f"user:{user_id}"
        if session.scalar(select(Actor.id).where(Actor.code == code)) is None:
            session.add(Actor(kind=ActorKind.USER.value, code=code, display_name=short_name, user_id=user_id))
            session.commit()


class DeviceAdmin(AuditedModelView, model=Device):
    name = "Устройство"
    name_plural = "Устройства"
    category = "Хора и достъп"
    icon = "fa-solid fa-mobile-screen"

    can_create = False
    can_edit = False  # единствената промяна е действието „Отнеми достъпа“
    column_list = [
        Device.user,
        Device.platform,
        Device.device_model,
        Device.push_provider,
        Device.last_seen_at,
        Device.revoked_at,
    ]
    column_searchable_list = [Device.device_model]
    column_details_list = [
        *(c.key for c in Device.__table__.columns if c.key != "push_token"),
        Device.user,
    ]
    column_labels = {
        "user": "Потребител",
        "platform": "Платформа",
        "device_model": "Модел",
        "push_provider": "Пуш доставчик",
        "last_seen_at": "Последно видяно",
        "revoked_at": "Отнето на",
    }

    @action(
        "revoke",
        "Отнеми достъпа",
        confirmation_message="Устройството ще бъде отписано и ще трябва да влезе отново. Сигурни ли сте?",
        add_in_list=True,
        add_in_detail=True,
    )
    async def revoke(self, request: Request) -> Response:
        pks = [int(p) for p in request.query_params.get("pks", "").split(",") if p]
        await anyio.to_thread.run_sync(revoke_devices, pks)
        referer = request.headers.get("Referer")
        return RedirectResponse(referer or str(request.url_for("admin:list", identity=self.identity)), 302)


def revoke_devices(device_ids: list[int]) -> int:
    """`revoked_at=now` на устройствата и на техните неотнети refresh токени (по обекти — одит)."""
    now = clock.now()
    count = 0
    with get_sessionmaker()() as session:
        for device in session.scalars(select(Device).where(Device.id.in_(device_ids))):
            if device.revoked_at is None:
                device.revoked_at = now
                count += 1
            for token in session.scalars(
                select(RefreshToken).where(
                    RefreshToken.device_id == device.id, RefreshToken.revoked_at.is_(None)
                )
            ):
                token.revoked_at = now
        session.commit()
    return count


class CompanyAdmin(AuditedModelView, model=CompanySettings):
    name = "Фирма"
    name_plural = "Фирма"
    category = "Настройки"
    icon = "fa-solid fa-building"

    can_create = False  # единствен ред
    column_list = [CompanySettings.legal_name, CompanySettings.eik, CompanySettings.system_mailbox_address]
    column_labels = {
        "legal_name": "Юридическо име",
        "short_name": "Кратко име",
        "eik": "ЕИК",
        "vat_number": "ДДС номер",
        "address_city": "Град",
        "address_line": "Адрес",
        "mol": "МОЛ",
        "bank_name": "Банка",
        "iban": "IBAN",
        "bic": "BIC",
        "email_signature": "Подпис на писмата",
        "system_mailbox_address": "Системна кутия",
        "accountant_email": "Имейл на счетоводителя",
    }


class NumberSequenceAdmin(AuditedModelView, model=NumberSequence):
    name = "Номерация"
    name_plural = "Номерации"
    category = "Настройки"
    icon = "fa-solid fa-list-ol"

    column_list = [
        NumberSequence.code,
        NumberSequence.next_number,
        NumberSequence.range_start,
        NumberSequence.range_end,
    ]
    column_labels = {
        "code": "Код",
        "description": "Описание",
        "next_number": "Следващ номер",
        "range_start": "Начало на диапазона",
        "range_end": "Край на диапазона",
        "width": "Широчина",
        "is_active": "Активна",
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        if is_created:
            return
        last = await anyio.to_thread.run_sync(_last_issued, model.id)
        next_number = data.get("next_number")
        if last is not None and next_number is not None and int(next_number) <= last:
            raise ValueError(f"Следващият номер не може да е под последния издаден ({last}).")


def _last_issued(sequence_id: int) -> int | None:
    with get_sessionmaker()() as session:
        return session.scalar(
            select(func.max(Invoice.number)).where(Invoice.number_sequence_id == sequence_id)
        )
