"""Файлове (docs/data-model.md §10, таблица `stored_objects`)."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, enum_check


class StoredObjectKind(StrEnum):
    EMAIL_RAW = "email_raw"
    ATTACHMENT = "attachment"
    INVOICE_PDF = "invoice_pdf"
    MEGA_XML = "mega_xml"
    OTHER = "other"


class StorageLocation(StrEnum):
    S3 = "s3"
    LOCAL = "local"


class StoredObject(AuditMixin, Base):
    __tablename__ = "stored_objects"
    __table_args__ = (
        enum_check("kind", StoredObjectKind),
        enum_check("location", StorageLocation),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Ключ в bucket-а, дори преди успешен upload; ползва се и като относителен локален път.
    s3_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_upload_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_upload_at: Mapped[datetime | None] = mapped_column(nullable=True)
