"""PR 30: core/log_context.py. Only ids get into the context (an email, a
token or text never does), a binding is undone by its token or scope, a job's
meta carries the ids without the job id, and the record factory stamps the
caller's context onto each record."""

import asyncio
import base64
import logging
import uuid

import pytest
from tenderos_backend.core.log_context import (
    JOB_META_KEY,
    RECORD_ATTRIBUTE,
    bind_log_context,
    current_log_context,
    install_log_record_factory,
    is_valid_request_id,
    job_meta,
    log_context,
    log_context_from,
    log_context_scope,
    new_request_id,
    reset_log_context,
)

# The W3C trace-id shape: 32 lowercase hex characters, not all zeros. A
# low-entropy value on purpose: a random-looking one reads as a secret to
# the secrets scan.
_REQUEST_ID = "ab" * 16
# A JSON Web Token's shape (three base64url parts), built here rather than
# written out, for the same reason.
_TOKEN_SHAPED = ".".join(
    base64.urlsafe_b64encode(part).decode().rstrip("=")
    for part in (b'{"alg":"HS256"}', b'{"sub":"1"}', b"signature")
)


@pytest.fixture(autouse=True)
def _empty_context():
    """Each test starts from an empty context, whatever ran before it (an
    invalid value clears its field), and the context is put back after."""
    token = bind_log_context(**dict.fromkeys(current_log_context(), "not an id"))
    yield
    reset_log_context(token)


def test_a_uuid_is_stored_in_its_canonical_form():
    run_id = uuid.uuid4()
    with log_context(run_id=run_id, tender_id=str(run_id).upper(), org_id=run_id.hex):
        assert current_log_context() == {
            "run_id": str(run_id),
            "tender_id": str(run_id),
            "org_id": str(run_id),
        }


@pytest.mark.parametrize(
    "value",
    [
        "someone@example.com",
        _TOKEN_SHAPED,
        "Section 4.2: the contractor shall supply",
        "x" * 500,
        12345,
        b"\x00",
        "",
    ],
)
def test_anything_but_an_id_is_dropped(value):
    with log_context(run_id=value, tender_id=value, request_id=value, job_id=value):
        assert current_log_context() == {}


@pytest.mark.parametrize(
    "value, valid",
    [
        (_REQUEST_ID, True),
        ("0" * 32, False),  # all zeros: W3C's invalid trace-id
        (_REQUEST_ID.upper(), False),
        (_REQUEST_ID[:-1], False),
        (_REQUEST_ID + "0", False),
        (str(uuid.UUID(_REQUEST_ID)), False),  # hyphens
        (_REQUEST_ID[:-1] + "g", False),
        (None, False),
    ],
)
def test_the_request_id_shape(value, valid):
    assert is_valid_request_id(value) is valid


def test_a_new_request_id_has_the_shape():
    ids = {new_request_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(is_valid_request_id(i) for i in ids)


def test_job_ids_take_rqs_characters_only():
    with log_context(job_id="resume_a1b2_c3d4-e5"):
        assert current_log_context() == {"job_id": "resume_a1b2_c3d4-e5"}
    with log_context(job_id="job:1"):
        assert current_log_context() == {}


def test_an_unknown_field_is_an_error():
    with pytest.raises(TypeError, match="runid"):
        bind_log_context(runid=uuid.uuid4())


def test_none_leaves_a_field_and_an_invalid_value_removes_it():
    first, second = uuid.uuid4(), uuid.uuid4()
    with log_context(run_id=first, tender_id=second):
        with log_context(run_id=None, tender_id="not an id"):
            assert current_log_context() == {"run_id": str(first)}
        assert current_log_context() == {"run_id": str(first), "tender_id": str(second)}


def test_the_token_undoes_the_binding():
    token = bind_log_context(request_id=_REQUEST_ID)
    assert current_log_context() == {"request_id": _REQUEST_ID}
    reset_log_context(token)
    assert current_log_context() == {}


def test_a_scoped_function_undoes_what_it_binds_even_when_it_raises():
    @log_context_scope
    async def binds_then_fails() -> None:
        bind_log_context(request_id=_REQUEST_ID)
        assert current_log_context() == {"request_id": _REQUEST_ID}
        raise RuntimeError("after binding")

    async def run() -> dict[str, str]:
        with pytest.raises(RuntimeError):
            await binds_then_fails()
        return current_log_context()

    assert asyncio.run(run()) == {}


def test_job_meta_carries_the_ids_without_the_job_id():
    run_id = uuid.uuid4()
    with log_context(request_id=_REQUEST_ID, run_id=run_id, job_id="workflow_x"):
        assert job_meta() == {JOB_META_KEY: {"request_id": _REQUEST_ID, "run_id": str(run_id)}}


def test_ids_read_back_from_meta_are_checked_again():
    run_id = uuid.uuid4()
    meta = {
        "request_id": _REQUEST_ID,
        "run_id": str(run_id),
        "tender_id": "someone@example.com",
        "unexpected": "ignored",
    }
    assert log_context_from(meta) == {"request_id": _REQUEST_ID, "run_id": str(run_id)}
    assert log_context_from("not a mapping") == {}
    assert log_context_from(None) == {}


def test_the_factory_stamps_the_callers_context_on_each_record(caplog):
    install_log_record_factory()
    install_log_record_factory()  # once only: a second call does not wrap again
    logger = logging.getLogger("test_log_context.factory")
    run_id = uuid.uuid4()
    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info("before")
        with log_context(request_id=_REQUEST_ID, run_id=run_id):
            logger.info("inside")
        logger.info("after")
    contexts = [getattr(r, RECORD_ATTRIBUTE) for r in caplog.records]
    assert contexts == [{}, {"request_id": _REQUEST_ID, "run_id": str(run_id)}, {}]


def test_concurrent_tasks_keep_their_own_ids():
    """Two requests or jobs on one event loop never see each other's ids."""

    async def one(request_id: str) -> str | None:
        bind_log_context(request_id=request_id)
        await asyncio.sleep(0)
        return current_log_context().get("request_id")

    async def both() -> list[str | None]:
        return list(await asyncio.gather(one("a" * 32), one("b" * 32)))

    assert asyncio.run(both()) == ["a" * 32, "b" * 32]
