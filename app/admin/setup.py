"""Монтиране на SQLAdmin на /admin (docs/data-model.md §14)."""

from collections.abc import Iterable

from fastapi import FastAPI
from sqladmin import Admin

from app.admin.auth import AdminAuth
from app.admin.base import AuditedModelView
from app.admin.views import ALL_VIEWS
from app.core.config import Env, get_settings
from app.db.session import get_engine


def setup_admin(app: FastAPI, views: Iterable[type[AuditedModelView]] = ALL_VIEWS) -> Admin:
    settings = get_settings()
    auth = AdminAuth(
        secret_key=settings.admin_session_secret.get_secret_value(),
        https_only=settings.env == Env.PROD,
        same_site="lax",
    )
    admin = Admin(app, get_engine(), authentication_backend=auth, title="Агро Импорт — админ")
    for view in views:
        admin.add_view(view)
    return admin
