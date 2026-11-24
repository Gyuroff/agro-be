"""Създава потребител: паролата се пита интерактивно.

`PYTHONPATH=. uv run python scripts/create_user.py --email a@b.bg --name "Име" --short-name И --admin`
"""

import argparse
import sys
from getpass import getpass

from sqlalchemy import select

from app.core.audit_context import AuditSource, actor_ctx_by_code, audit_context
from app.db.session import session_scope
from app.models.identity import User
from app.services.users import UserService


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True, help="пълно име")
    ap.add_argument("--short-name", required=True)
    ap.add_argument("--admin", action="store_true")
    args = ap.parse_args()

    password = getpass("Парола: ")
    if not password or password != getpass("Повторете паролата: "):
        sys.exit("Паролите не съвпадат или са празни.")

    with session_scope() as session:
        ctx = actor_ctx_by_code("system:seed", AuditSource.SHELL, session=session, reason="create_user")
        with audit_context(ctx):
            if session.scalar(select(User.id).where(User.email == args.email)) is not None:
                sys.exit(f"Потребител {args.email} вече съществува.")
            user = UserService.create(
                session,
                email=args.email,
                full_name=args.name,
                short_name=args.short_name,
                password=password,
                is_admin=args.admin,
            )
            print(f"Създаден потребител id={user.id} ({'админ' if args.admin else 'обикновен'}).")


if __name__ == "__main__":
    main()
