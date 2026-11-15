"""Файлове: S3 първо, локален диск само като опашка при S3 fail (docs/architecture.md §11).

Запис:   PUT към S3 → `location=s3`; при грешка → локален файл, `location=local`, `upload_attempts=1`.
Четене:  `open()` — първо S3; ако го няма там → локалният, веднага нов PUT; успех → стрийм от S3 и
         трие локалния; провал → стрийм от локалния, `upload_attempts += 1`; при `>= 3` се вика
         `on_exhausted` (карта `storage_upload_failed` + пуш P13 — вързва се със задачите за карти/пуш).
Retry:   `retry_local()` (вика я Celery `storage.retry_local` на 1 мин.).

Функциите не commit-ват: транзакцията е на заявката/задачата (HANDOFF).
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

import boto3  # pyright: ignore[reportMissingTypeStubs]
from botocore.config import Config  # pyright: ignore[reportMissingTypeStubs]
from botocore.exceptions import BotoCoreError, ClientError  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import Settings, get_settings
from app.models.storage import StorageLocation, StoredObject, StoredObjectKind

log = logging.getLogger(__name__)

MAX_UPLOAD_ATTEMPTS = 3
_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


class ByteStream(Protocol):
    def read(self, amt: int | None = None, /) -> bytes: ...
    def close(self) -> None: ...


class StorageError(Exception):
    """Файлът не може да бъде прочетен нито от S3, нито локално."""


ExhaustedHandler = Callable[[Session, StoredObject], None]


def make_s3_client(settings: Settings) -> Any:
    """boto3 клиент за Hetzner Object Storage (s3v4, virtual addressing)."""
    client = boto3.client(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key.get_secret_value() if settings.s3_access_key else None,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 1},
        ),
    )
    return cast(Any, client)


def _is_missing(exc: ClientError) -> bool:
    response: Any = exc.response
    return str(response.get("Error", {}).get("Code", "")) in _MISSING_CODES


class Storage:
    def __init__(
        self,
        client: Any,
        *,
        bucket: str | None,
        local_dir: str | Path,
        on_exhausted: ExhaustedHandler | None = None,
    ) -> None:
        self._s3 = client
        self._bucket = bucket
        self._local_dir = Path(local_dir)
        self._on_exhausted = on_exhausted

    @classmethod
    def from_settings(cls, on_exhausted: ExhaustedHandler | None = None) -> Storage:
        s = get_settings()
        return cls(make_s3_client(s), bucket=s.s3_bucket, local_dir=s.storage_dir, on_exhausted=on_exhausted)

    # ── S3 ────────────────────────────────────────────────────────────────────────────────────
    def _put(self, key: str, data: bytes, content_type: str | None) -> None:
        if not self._bucket:
            raise RuntimeError("APP_S3_BUCKET не е зададен")
        extra: dict[str, str] = {"ContentType": content_type} if content_type else {}
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)

    def _get(self, key: str) -> ByteStream | None:
        """Стрийм от S3; None ако ключът не съществува. Друга грешка (5xx, мрежа) се вдига."""
        if not self._bucket:
            raise RuntimeError("APP_S3_BUCKET не е зададен")
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"]
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise

    # ── локален диск ─────────────────────────────────────────────────────────────────────────
    def _local_file(self, key: str) -> Path:
        path = (self._local_dir / key).resolve()
        if not path.is_relative_to(self._local_dir.resolve()):
            raise ValueError(f"небезопасен ключ: {key!r}")
        return path

    def _write_local(self, key: str, data: bytes) -> Path:
        path = self._local_file(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path

    # ── състояние на реда ────────────────────────────────────────────────────────────────────
    @staticmethod
    def _record_failure(obj: StoredObject, exc: Exception) -> None:
        obj.upload_attempts += 1
        obj.last_upload_error = f"{type(exc).__name__}: {exc}"[:1000]
        obj.last_upload_at = clock.now()

    def _mark_uploaded(self, obj: StoredObject) -> None:
        if obj.local_path:
            Path(obj.local_path).unlink(missing_ok=True)
        obj.location = StorageLocation.S3
        obj.local_path = None
        obj.last_upload_error = None
        obj.last_upload_at = clock.now()

    def _check_exhausted(self, session: Session, obj: StoredObject) -> None:
        if obj.upload_attempts >= MAX_UPLOAD_ATTEMPTS and self._on_exhausted is not None:
            self._on_exhausted(session, obj)

    # ── публичен API ─────────────────────────────────────────────────────────────────────────
    def save(
        self,
        session: Session,
        *,
        kind: StoredObjectKind,
        data: bytes,
        content_type: str | None = None,
    ) -> StoredObject:
        """Запис: S3 първо; при грешка → локален диск + `upload_attempts=1`."""
        key = f"{kind.value}/{uuid.uuid4().hex}"
        obj = StoredObject(
            kind=kind.value,
            s3_key=key,
            location=StorageLocation.S3,
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            upload_attempts=0,
        )
        try:
            self._put(key, data, content_type)
            obj.last_upload_at = clock.now()
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            log.warning("S3 PUT failed for %s, falling back to local: %s", key, exc)
            obj.local_path = str(self._write_local(key, data))
            obj.location = StorageLocation.LOCAL
            self._record_failure(obj, exc)
        session.add(obj)
        session.flush()
        return obj

    def open(self, session: Session, stored_object_id: int) -> ByteStream:
        """Четене по §11. Извикващият затваря стрийма."""
        obj = session.get(StoredObject, stored_object_id)
        if obj is None:
            raise StorageError(f"stored_object {stored_object_id} не съществува")

        s3_failure: Exception | None = None
        try:
            body = self._get(obj.s3_key)
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            body, s3_failure = None, exc
        if body is not None:
            if obj.location != StorageLocation.S3:  # качен междувременно — изравняваме реда
                self._mark_uploaded(obj)
                session.flush()
            return body

        # Няма го в S3 (или S3 е недостъпно) → трябва да е локално.
        if obj.location != StorageLocation.LOCAL or not obj.local_path or not Path(obj.local_path).exists():
            raise StorageError(f"файлът {obj.s3_key} не е нито в S3, нито локално") from s3_failure
        local = Path(obj.local_path)

        try:
            self._put(obj.s3_key, local.read_bytes(), obj.content_type)
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            self._record_failure(obj, exc)
            session.flush()
            self._check_exhausted(session, obj)
            return local.open("rb")

        self._mark_uploaded(obj)
        session.flush()
        body = self._get(obj.s3_key)
        if body is None:  # PUT мина, а GET не намира ключа — не би трябвало да се случва
            raise StorageError(f"{obj.s3_key} липсва в S3 след успешен PUT")
        return body

    def force_upload(self, session: Session, stored_object_id: int) -> bool:
        """Принудителен PUT на локален файл (карта `storage_upload_failed` → `retry`). True при успех."""
        obj = session.get(StoredObject, stored_object_id)
        if obj is None:
            raise StorageError(f"stored_object {stored_object_id} не съществува")
        return self._upload_local(session, obj)

    def retry_local(self, session: Session, *, limit: int = 100) -> int:
        """Celery `storage.retry_local`: качва всички `location=local`; връща броя успешни."""
        rows = session.scalars(
            select(StoredObject)
            .where(StoredObject.location == StorageLocation.LOCAL)
            .order_by(StoredObject.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        return sum(1 for obj in rows if self._upload_local(session, obj))

    def _upload_local(self, session: Session, obj: StoredObject) -> bool:
        if obj.location != StorageLocation.LOCAL or not obj.local_path:
            return True  # вече е в S3
        local = Path(obj.local_path)
        try:
            self._put(obj.s3_key, local.read_bytes(), obj.content_type)
        except (BotoCoreError, ClientError, RuntimeError, OSError) as exc:
            self._record_failure(obj, exc)
            session.flush()
            self._check_exhausted(session, obj)
            return False
        self._mark_uploaded(obj)
        session.flush()
        return True
