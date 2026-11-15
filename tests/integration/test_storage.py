"""storage: S3 първо, локален fallback, open-retry, 3 неуспеха → on_exhausted (agro-1k3).

S3 е moto; „S3 е паднало“ се имитира с обвивка, която вдига 503. Одитните id-та идват от `audit_ctx`.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import boto3  # pyright: ignore[reportMissingTypeStubs]
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.core.storage import Storage, StorageError
from app.models.storage import StorageLocation, StoredObject, StoredObjectKind

BUCKET = "agro-test"
DATA = b"%PDF-1.7 test"


class FlakyS3:
    """Делегира към moto клиента; при `down=True` всяка заявка е 503."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.down = False

    def __getattr__(self, name: str) -> Callable[..., Any]:
        target = getattr(self._inner, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            if self.down:
                raise ClientError({"Error": {"Code": "503", "Message": "SlowDown"}}, name)
            return target(*args, **kwargs)

        return call


@pytest.fixture
def s3() -> Iterator[FlakyS3]:
    with mock_aws():
        client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
            "s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
        )
        client.create_bucket(Bucket=BUCKET)
        yield FlakyS3(client)


@pytest.fixture
def exhausted() -> list[int]:
    return []


@pytest.fixture
def storage(s3: FlakyS3, tmp_path: Path, exhausted: list[int]) -> Storage:
    return Storage(
        s3,
        bucket=BUCKET,
        local_dir=tmp_path,
        on_exhausted=lambda _session, obj: exhausted.append(obj.id),
    )


@pytest.fixture
def session(db_session: Session, audit_ctx: AuditContext) -> Session:
    return db_session


def _s3_has(s3: FlakyS3, key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
    except ClientError:
        return False
    return True


def _local_obj(storage: Storage, s3: FlakyS3, session: Session) -> StoredObject:
    s3.down = True
    obj = storage.save(session, kind=StoredObjectKind.INVOICE_PDF, data=DATA, content_type="application/pdf")
    s3.down = False
    return obj


def test_save_s3_ok(storage: Storage, s3: FlakyS3, session: Session, tmp_path: Path) -> None:
    obj = storage.save(session, kind=StoredObjectKind.INVOICE_PDF, data=DATA, content_type="application/pdf")
    assert obj.location == StorageLocation.S3
    assert obj.local_path is None
    assert obj.upload_attempts == 0
    assert obj.size_bytes == len(DATA)
    assert _s3_has(s3, obj.s3_key)
    assert not any(tmp_path.rglob("*.*")) and not any(p.is_file() for p in tmp_path.rglob("*"))


def test_save_s3_fail_goes_local(storage: Storage, s3: FlakyS3, session: Session) -> None:
    obj = _local_obj(storage, s3, session)
    assert obj.location == StorageLocation.LOCAL
    assert obj.upload_attempts == 1
    assert obj.last_upload_error
    assert obj.local_path
    assert Path(obj.local_path).read_bytes() == DATA
    assert not _s3_has(s3, obj.s3_key)


def test_open_from_s3(storage: Storage, session: Session) -> None:
    obj = storage.save(session, kind=StoredObjectKind.ATTACHMENT, data=DATA)
    stream = storage.open(session, obj.id)
    assert stream.read() == DATA


def test_open_local_upload_success_streams_s3_and_deletes_local(
    storage: Storage, s3: FlakyS3, session: Session
) -> None:
    obj = _local_obj(storage, s3, session)
    local = Path(obj.local_path or "")
    stream = storage.open(session, obj.id)
    assert stream.read() == DATA
    assert obj.location == StorageLocation.S3
    assert obj.local_path is None
    assert not local.exists()
    assert _s3_has(s3, obj.s3_key)


def test_open_local_upload_fail_streams_local_and_counts(
    storage: Storage, s3: FlakyS3, session: Session, exhausted: list[int]
) -> None:
    obj = _local_obj(storage, s3, session)
    s3.down = True
    stream = storage.open(session, obj.id)
    assert stream.read() == DATA
    assert obj.location == StorageLocation.LOCAL
    assert obj.upload_attempts == 2
    assert exhausted == []


def test_three_failures_trigger_exhausted_handler(
    storage: Storage, s3: FlakyS3, session: Session, exhausted: list[int]
) -> None:
    obj = _local_obj(storage, s3, session)  # 1
    s3.down = True
    storage.open(session, obj.id).close()  # 2
    assert exhausted == []
    storage.open(session, obj.id).close()  # 3
    assert obj.upload_attempts == 3
    assert exhausted == [obj.id]


def test_retry_local_uploads_and_deletes_local(storage: Storage, s3: FlakyS3, session: Session) -> None:
    obj = _local_obj(storage, s3, session)
    local = Path(obj.local_path or "")
    assert storage.retry_local(session) == 1
    assert obj.location == StorageLocation.S3
    assert not local.exists()
    assert _s3_has(s3, obj.s3_key)
    assert storage.retry_local(session) == 0


def test_retry_local_failure_counts_and_escalates(
    storage: Storage, s3: FlakyS3, session: Session, exhausted: list[int]
) -> None:
    obj = _local_obj(storage, s3, session)
    s3.down = True
    assert storage.retry_local(session) == 0
    assert storage.retry_local(session) == 0
    assert obj.upload_attempts == 3
    assert exhausted == [obj.id]
    assert obj.location == StorageLocation.LOCAL


def test_force_upload(storage: Storage, s3: FlakyS3, session: Session) -> None:
    obj = _local_obj(storage, s3, session)
    assert storage.force_upload(session, obj.id) is True
    assert obj.location == StorageLocation.S3


def test_open_missing_everywhere_raises(storage: Storage, session: Session) -> None:
    obj = storage.save(session, kind=StoredObjectKind.OTHER, data=DATA)
    storage._s3.delete_object(Bucket=BUCKET, Key=obj.s3_key)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(StorageError):
        storage.open(session, obj.id)


def test_local_key_traversal_rejected(storage: Storage) -> None:
    with pytest.raises(ValueError):
        storage._local_file("../evil")  # pyright: ignore[reportPrivateUsage]
