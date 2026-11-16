"""Потребители (docs/data-model.md §1, §3)."""

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.identity import Actor, ActorKind, User


class UserService:
    @staticmethod
    def create(
        session: Session,
        *,
        email: str,
        full_name: str,
        short_name: str,
        password: str,
        is_admin: bool = False,
    ) -> User:
        """Създава `users` и реда в `actors` (kind=user, code=`user:<id>`) в една транзакция.

        Извиква се в одитен контекст; commit е на извикващия.
        """
        user = User(
            email=email,
            full_name=full_name,
            short_name=short_name,
            password_hash=hash_password(password),
            is_admin=is_admin,
        )
        session.add(user)
        session.flush()
        session.add(
            Actor(kind=ActorKind.USER.value, code=f"user:{user.id}", display_name=short_name, user_id=user.id)
        )
        session.flush()
        return user
