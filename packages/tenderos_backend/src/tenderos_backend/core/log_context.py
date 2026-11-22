"""Observability phase, PR 30: the ids every log line carries.

One `contextvars` context holds up to seven ids for whatever the process is
doing right now: the api request (`request_id`), the workflow run, tender,
organization, async operation and reanalysis claim it concerns, and the RQ
job running it (`job_id`). A log record factory copies that context onto
every record at the moment of the logging call (install_log_record_factory),
so each line says which request and run it belongs to, and one request's
lines can be read end to end from the api through the outbox to the job.

Only ids get in. Every value is checked when it is bound: `request_id` must
be 32 lowercase hex characters (the W3C trace-id shape, so tracing can adopt
it later; all zeros is refused, as W3C does), `job_id` RQ's own id
characters, and every other field a UUID, stored in its canonical form. A
value that fails is dropped, never logged, so an email, a token or a piece of
document text cannot reach a log line through this context, whatever a
caller passes.

Why a contextvar: an asyncio task and a thread started with
`asyncio.to_thread` each start from a copy of the caller's context, so ids
bound for one request or job never leak into another running beside it, and
nothing has to thread them through every function signature. A forked RQ
work horse keeps its parent's context too, which is why the worker binds a
job's ids before the fork (worker.py).

Why a record factory, not a handler filter: a factory runs when the record
is made, so the context is the caller's own, and every handler sees it,
including pytest's capture handler in the tests. A filter runs only on the
handlers it was attached to.
"""

import functools
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from types import MappingProxyType

LOG_CONTEXT_FIELDS = (
    "request_id",
    "run_id",
    "tender_id",
    "org_id",
    "operation_id",
    "reanalysis_claim_id",
    "job_id",
)
_UUID_FIELDS = frozenset(LOG_CONTEXT_FIELDS) - {"request_id", "job_id"}

# The W3C trace-id shape: 32 lowercase hex characters, not all zeros.
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_ZERO_REQUEST_ID = "0" * 32
# RQ refuses any other character in a job id; the longest this codebase
# builds is two UUIDs and a short prefix (resume_job_id), about 80.
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
# A UUID's longest accepted spelling is the urn form, 45 characters; longer
# input is refused before parsing.
_MAX_UUID_INPUT = 45

# The record attribute the factory sets; formatters read it from there.
RECORD_ATTRIBUTE = "log_context"
# The key under which a job's ids travel in its RQ meta (job_meta()).
JOB_META_KEY = "log_context"

_EMPTY: Mapping[str, str] = MappingProxyType({})
_CONTEXT: ContextVar[Mapping[str, str]] = ContextVar("tenderos_log_context", default=_EMPTY)


def is_valid_request_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and _REQUEST_ID.fullmatch(value) is not None
        and value != _ZERO_REQUEST_ID
    )


def new_request_id() -> str:
    """32 lowercase hex characters, random (uuid4), never all zeros."""
    return uuid.uuid4().hex


def _clean(name: str, value: object) -> str | None:
    """The value as it may be logged, or None when it is not an id."""
    if name == "request_id":
        return value if isinstance(value, str) and is_valid_request_id(value) else None
    if name == "job_id":
        if isinstance(value, str) and _JOB_ID.fullmatch(value):
            return value
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str) and len(value) <= _MAX_UUID_INPUT:
        try:
            return str(uuid.UUID(value))
        except ValueError:
            return None
    return None


def bind_log_context(**fields: object) -> Token[Mapping[str, str]]:
    """Add ids to the current context and return the token that undoes it
    (reset_log_context). A field passed as None is left as it was. A field
    whose value is not a valid id is removed, not kept from before: a stale
    id from an earlier step would put a line under the wrong run.

    An unknown field name is a TypeError: a typo here would otherwise drop
    an id silently."""
    unknown = sorted(set(fields) - set(LOG_CONTEXT_FIELDS))
    if unknown:
        raise TypeError(f"unknown log context field(s): {', '.join(unknown)}")
    merged = dict(_CONTEXT.get())
    for name, value in fields.items():
        if value is None:
            continue
        cleaned = _clean(name, value)
        if cleaned is None:
            merged.pop(name, None)
        else:
            merged[name] = cleaned
    return _CONTEXT.set(MappingProxyType(merged))


def reset_log_context(token: Token[Mapping[str, str]]) -> None:
    _CONTEXT.reset(token)


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """bind_log_context for the length of a `with` block."""
    token = bind_log_context(**fields)
    try:
        yield
    finally:
        reset_log_context(token)


def log_context_scope[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Run an async function with its own copy of the context: whatever it
    binds is undone when it returns or raises. For a function that learns
    its ids part way through (the dispatcher learns an event's only once it
    has claimed it), so it can bind them where it learns them."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        token = _CONTEXT.set(_CONTEXT.get())
        try:
            return await fn(*args, **kwargs)
        finally:
            _CONTEXT.reset(token)

    return wrapper


def current_log_context() -> dict[str, str]:
    return dict(_CONTEXT.get())


def log_context_from(source: object) -> dict[str, str]:
    """The valid ids in a mapping that came from outside this process (a
    job's RQ meta): unknown keys are ignored and invalid values dropped,
    never raised on, since a job must not fail over its log context."""
    if not isinstance(source, Mapping):
        return {}
    cleaned: dict[str, str] = {}
    for name in LOG_CONTEXT_FIELDS:
        value = source.get(name)
        if value is None:
            continue
        valid = _clean(name, value)
        if valid is not None:
            cleaned[name] = valid
    return cleaned


def job_meta() -> dict[str, dict[str, str]]:
    """The RQ meta for a job enqueued from here: this context's ids, without
    `job_id` (the new job has its own, which the worker binds from job.id)."""
    ids = current_log_context()
    ids.pop("job_id", None)
    return {JOB_META_KEY: ids}


_FACTORY_MARK = "_tenderos_log_context_factory"


def install_log_record_factory() -> None:
    """Wrap the process's log record factory, once, so every record carries
    the context as `record.log_context` (a dict of the bound ids). One
    attribute, not seven: a caller's own `extra={"run_id": ...}` would
    otherwise collide with it, which logging refuses with a KeyError."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, _FACTORY_MARK, False):
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        setattr(record, RECORD_ATTRIBUTE, dict(_CONTEXT.get()))
        return record

    setattr(factory, _FACTORY_MARK, True)
    logging.setLogRecordFactory(factory)
