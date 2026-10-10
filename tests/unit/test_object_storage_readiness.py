"""/health/ready's object-storage probe (tenderos_api.main._check_object_storage).

An authenticated HeadBucket replaced MinIO's /minio/health/live, so readiness
no longer depends on one vendor's private health path. The boto3 client is
patched: these tests pin the decision table, not a real server (the real
server is exercised by the integration suite and the stack checks).
"""

import asyncio
import logging

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from tenderos_api import main
from tenderos_backend.core.config import settings as backend_settings


@pytest.fixture(autouse=True)
def _logger_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Alembic's migrations/env.py calls logging.config.fileConfig(), whose
    # default disable_existing_loggers=True disables every logger that exists
    # at that moment. When an earlier test in the same process runs the
    # migrations (tests/integration/test_schema_diff.py), tenderos_api.main's
    # logger is left disabled and these log assertions see nothing. Not a
    # production path (the api never runs Alembic in-process); recorded in
    # KNOWN_LIMITATIONS.md. Re-enable it for each test here.
    monkeypatch.setattr(main.logger, "disabled", False)


def _client_error(status: int, code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": ""}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadBucket",
    )


class _StubClient:
    def __init__(self, outcome: Exception | None) -> None:
        self.outcome = outcome
        self.buckets: list[str] = []

    def head_bucket(self, Bucket: str) -> dict[str, object]:  # noqa: N803 - boto3's own keyword
        self.buckets.append(Bucket)
        if self.outcome is not None:
            raise self.outcome
        return {}


def _probe(monkeypatch: pytest.MonkeyPatch, outcome: Exception | None) -> tuple[bool, _StubClient]:
    stub = _StubClient(outcome)
    monkeypatch.setattr(main, "_object_storage_probe_client", lambda: stub)
    return asyncio.run(main._check_object_storage()), stub


def _assert_no_secret_logged(caplog: pytest.LogCaptureFixture) -> None:
    assert backend_settings.s3_secret_key not in caplog.text


def test_existing_bucket_is_ready_and_the_app_bucket_is_the_one_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready, stub = _probe(monkeypatch, None)

    assert ready is True
    assert stub.buckets == [backend_settings.s3_bucket]


def test_missing_bucket_404_is_ready_because_the_bucket_is_created_on_first_upload(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=main.logger.name):
        ready, _ = _probe(monkeypatch, _client_error(404, "404"))

    assert ready is True
    assert caplog.records == []


def test_wrong_credentials_403_is_not_ready_and_logs_status_without_the_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=main.logger.name):
        ready, _ = _probe(monkeypatch, _client_error(403, "403"))

    assert ready is False
    assert "HTTP 403" in caplog.text
    _assert_no_secret_logged(caplog)


def test_any_other_http_error_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=main.logger.name):
        ready, _ = _probe(monkeypatch, _client_error(500, "InternalError"))

    assert ready is False
    assert "HTTP 500 (InternalError)" in caplog.text
    _assert_no_secret_logged(caplog)


def test_connection_failure_is_not_ready_and_logs_the_exception_type(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    error = EndpointConnectionError(endpoint_url="http://s3:9000/tenderos")
    with caplog.at_level(logging.WARNING, logger=main.logger.name):
        ready, _ = _probe(monkeypatch, error)

    assert ready is False
    assert "EndpointConnectionError" in caplog.text
    _assert_no_secret_logged(caplog)


def test_the_probe_client_is_built_once_from_the_storage_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_client(service: str, **kwargs: object) -> _StubClient:
        calls.append({"service": service, **kwargs})
        return _StubClient(None)

    monkeypatch.setattr(main.boto3, "client", fake_client)
    main._object_storage_probe_client.cache_clear()
    try:
        first = main._object_storage_probe_client()
        second = main._object_storage_probe_client()
    finally:
        main._object_storage_probe_client.cache_clear()

    assert first is second
    assert len(calls) == 1
    assert calls[0]["service"] == "s3"
    assert calls[0]["endpoint_url"] == backend_settings.s3_endpoint_url
    assert calls[0]["region_name"] == backend_settings.s3_region
