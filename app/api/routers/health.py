from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine

router = APIRouter(tags=["health"])


def _db_ok() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


@router.get("/health")
def health() -> dict[str, str | bool]:
    """Процесът отговаря (винаги 200); `db` казва дали базата е достъпна."""
    return {"status": "ok", "db": _db_ok()}
