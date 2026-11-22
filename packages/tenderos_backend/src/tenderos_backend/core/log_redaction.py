"""B15: log redaction, originally built as defense-in-depth against the
then-unconfigured logging state - not the primary fix for the one
concretely identified leak. B16 added configure_logging() below, which
closes the gap this docstring used to describe as open: apps/api and
apps/worker now call logging.basicConfig() (via configure_logging(), not
directly), so .info() calls reach a real handler instead of being silently
dropped before a LogRecord is even constructed. Before B16, every module
in this codebase relied on Python's own "handler of last resort"
(logging.lastResort) for WARNING+ visibility only, verified directly
during B15 planning via a throwaway script, not assumed.

The one leak this project had actually identified and traced - gemini_
adapter.py's MalformedOutputError embedding a pydantic ValidationError's
raw input_value - is fixed at its source (gemini_adapter.py's own
_redacted_validation_summary, `from None` instead of `from exc` so the
original exception's own unredacted traceback never attaches at all). That
fix does not depend on logging configuration in any way; this module exists
for what hasn't been individually audited - a different exception type, a
future call site that reintroduces the same mistake. Honestly scoped, not
claimed exhaustive: it pattern-matches pydantic's own
`input_value=..., input_type=X]` shape specifically, the one concrete
pattern this project has real evidence for, not a general "detect anything
sensitive" classifier.

Observability phase, PR 30: the filter also redacts every `extra=` field,
since the JSON format (log_format.py) writes them all out, and
configure_logging() now sets the format (LOG_FORMAT), installs the log
context (log_context.py) and sends uvicorn's own loggers, the access log
included, through the same handler.
"""

import logging
import os
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TextIO

from tenderos_backend.core.log_context import install_log_record_factory
from tenderos_backend.core.log_format import STANDARD_RECORD_ATTRIBUTES, formatter_for

# Anchored on `, input_type=<identifier>]` - the one part of pydantic's
# ValidationError formatting that's always plain, predictable text,
# regardless of what the offending value itself contains (including a
# literal `]`, which a naive "match up to the first `]`" pattern would
# truncate on too early, leaving the rest of the raw value unredacted).
#
# Greedy `.*`, deliberately not non-greedy `.*?`: a non-greedy match stops
# at the FIRST `, input_type=\w+]`-shaped substring it finds, including one
# embedded inside the sensitive value's own repr (data that happens to
# contain that exact text, not pydantic's real closing marker) - leaving
# everything between that fake early stop and the real end unredacted, a
# real bypass since input_value can be an arbitrary repr of
# attacker-influenced data. A first attempt fixed this with a
# negative-lookahead loop (`(?:(?!, input_type=\w+\]).)*`) instead of
# `.*`, on the theory that it could only advance through non-terminator
# characters - that doesn't actually work: the loop stops at the exact
# same first position a fake terminator can start, and the terminator
# then matches there too, so it ends up identical to `.*?` on this exact
# case (verified by a failing test, not assumed). Plain greedy `.*`
# actually solves it: it first consumes to the end of the record, then
# backtracks to the LAST `, input_type=\w+]` in the text, which is always
# at least as much redaction as the real boundary requires.
#
# Tradeoff, accepted deliberately: if a single record legitimately
# contains multiple separate `input_value=...]` blocks (e.g. pydantic
# reporting several field errors in one ValidationError), greedy `.*`
# redacts the ordinary text BETWEEN them too, collapsing both blocks into
# one span - over-redaction, not under-redaction, the correct direction
# for a security control to err in.
_INPUT_VALUE_PATTERN = re.compile(r"input_value=.*, input_type=\w+\]", re.DOTALL)
_REDACTED = "input_value=[REDACTED]"


def _redact_text(text: str) -> str:
    return _INPUT_VALUE_PATTERN.sub(_REDACTED, text)


# Nesting deeper than this in an extra is flattened to its redacted repr.
_MAX_EXTRA_DEPTH = 6


def _redact_value(value: object, redact: Callable[[str], str], depth: int = 0) -> object:
    """An extra field's value, redacted: strings through `redact`, mappings
    and sequences item by item, numbers, booleans and None as they are, and
    anything else as its redacted str(). str() first, because the JSON
    format would call str() on it anyway (log_format.py), after this filter
    has run: an object whose text holds a secret would pass unredacted."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return redact(value)
    if depth >= _MAX_EXTRA_DEPTH:
        return redact(repr(value))
    if isinstance(value, dict):
        return {str(k): _redact_value(v, redact, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_redact_value(v, redact, depth + 1) for v in value]
    return redact(str(value))


def redact_record_extras(record: logging.LogRecord, redact: Callable[[str], str]) -> None:
    """Apply `redact` to every attribute a plain LogRecord does not have:
    each `extra=` field, and the log context (only ids, so a no-op there,
    but nothing is skipped by name). Shared with tenderos_api's SSE ticket
    filter."""
    for key in [k for k in vars(record) if k not in STANDARD_RECORD_ATTRIBUTES]:
        setattr(record, key, _redact_value(getattr(record, key), redact))


class RedactSensitiveDataFilter(logging.Filter):
    """Redacts the pydantic input_value=...]/input_type=...] pattern from a
    LogRecord's message and, separately, from its exception traceback if
    one is attached - the two need different handling. A record's message
    can be rewritten directly, but exc_info's traceback text is normally
    rendered later, by the handler's own Formatter, from the raw exception
    object - a filter that only touches record.msg would never see it.
    Pre-computing and caching record.exc_text here works because
    Formatter.format() only calls formatException() when record.exc_text
    is still unset (verified directly, not assumed) - whatever this filter
    puts there is what actually gets emitted, not silently regenerated
    unredacted by the handler afterward.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        if record.args:
            # LogRecord.args is a tuple for positional %-style args, but
            # can also be a single Mapping when a caller uses %-style named
            # placeholders (`logger.warning("...%(input_value)s...", {...})`)
            # - iterating a dict yields its KEYS, so the old unconditional
            # `tuple(...)` comprehension both dropped every value (nothing
            # actually got redacted) and replaced the mapping with a tuple
            # of key strings, which breaks `record.getMessage()`'s own
            # `msg % args` for any %(name)s-style format string.
            if isinstance(record.args, dict):
                record.args = {
                    k: (_redact_text(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    _redact_text(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        if record.exc_info and not record.exc_text:
            record.exc_text = _redact_text(logging.Formatter().formatException(record.exc_info))
        redact_record_extras(record, _redact_text)
        return True


def _attach_redaction_filter(handler: logging.Handler) -> None:
    if any(isinstance(f, RedactSensitiveDataFilter) for f in handler.filters):
        return
    handler.addFilter(RedactSensitiveDataFilter())


def install_log_redaction() -> None:
    """Call once, at process startup - apps/api/main.py, apps/worker/
    worker.py, apps/worker/outbox_dispatcher.py, the three real process
    entrypoints.

    Attached to logging.lastResort specifically, not the root logger:
    every module in this codebase does `logging.getLogger(__name__)`, and
    a Filter attached to one Logger instance does not apply to a
    different logger's own records - Python's propagation only walks
    HANDLERS up the ancestor chain (Logger.callHandlers), it never
    re-checks each ancestor logger's own Logger.filter(). lastResort is
    the one shared Handler object every currently-unconfigured logger's
    WARNING+ output actually flows through today (confirmed directly, not
    assumed - see this module's own docstring).

    Superseded, as of B16, by configure_logging() below at all three real
    entrypoints - kept here, unchanged, because B15's own tests exercise
    this exact lastResort-attachment behavior directly, and because it
    remains correct (if inert) once configure_logging() has run: lastResort
    stops being reached at that point, so attaching a filter to it a second
    time is harmless, not wrong.
    """
    handler = logging.lastResort
    if handler is None:
        # Typed Handler | None upstream (a caller could set it to None) -
        # not something this project's own processes ever do, but mypy
        # correctly won't assume that. Nothing to attach a filter to.
        return
    _attach_redaction_filter(handler)


def configure_logging(log_format: str | None = None) -> None:
    """B16: the real fix for the gap install_log_redaction's own docstring
    already named - .info() calls have been silently dropped since B0
    because nothing anywhere ever called logging.basicConfig(). Call once,
    at the same three real process entrypoints install_log_redaction
    documents (apps/api/main.py, apps/worker/worker.py, apps/worker/
    outbox_dispatcher.py) - REPLACES the install_log_redaction() call at
    each of those three sites, not an addition next to it.

    Order matters and is the whole point of this function existing as one
    unit rather than two separate calls a caller has to remember to
    sequence correctly: basicConfig() first, so the root logger has a real
    handler; then the redaction filter is attached to THAT handler, not to
    lastResort. Once basicConfig() attaches a handler anywhere in the
    hierarchy, Logger.callHandlers stops falling through to lastResort at
    all (per install_log_redaction's own docstring) - so a caller that
    called basicConfig() and install_log_redaction() separately, in either
    order, would silently stop being protected: the filter would still be
    sitting on lastResort, and lastResort would no longer be reached.
    Collapsing both steps into one function is what makes that ordering
    bug structurally impossible to reintroduce at a fourth call site later.

    PR 30 replaced basicConfig() with this module's own handler
    (build_log_handler), added the same way: only when the root logger has
    no handler yet. basicConfig() was a no-op whenever the root logger
    already had a handler (true inside a pytest session, which attaches its
    own root-logger handlers per test), and so is this: it is safe to call
    unconditionally at module-import time, and it does not fight pytest's
    own log capture, whose handlers get the redaction filter but keep their
    own format. Calling it again (a startup refusal configures logging
    early, log_startup_refusal) finds its own handler and only resets the
    format.

    That no-op case is exactly why the root logger's level is set as a
    separate, unconditional step below: whenever a handler already exists -
    the pytest case above, or a real deployment where some other library
    attached one first - the root logger would otherwise be left at its
    default WARNING. A Logger's effective level is checked before a
    LogRecord is even constructed (Logger.isEnabledFor, ahead of any handler
    or filter), so every .info() call in the codebase would be silently
    dropped at the logger itself, never reaching the redaction filter.

    Also, since PR 30:
    - `log_format` is LOG_FORMAT (core/config.py's log_format), `json` by
      default; a caller that already knows it passes it (the startup
      refusal, which runs while settings are still being checked).
    - The log record factory (log_context.py) is installed, so every record
      carries the request, run and job ids bound at the time.
    - uvicorn's own loggers lose their handlers and propagate to the root
      one, so its startup lines and its access log come out in the same
      format, through the same redaction. uvicorn configures them in
      Config.__init__, before it imports the app, so the api's import-time
      call here runs after (tenderos_api/logging_filters.py). The SSE
      ticket filter sits on the uvicorn.access logger itself and still runs
      first. uvicorn checks once per connection whether the access logger
      has a handler (hasHandlers(), which follows propagation), and the root
      handler counts.
    """
    chosen = _configured_log_format() if log_format is None else log_format
    formatter = formatter_for(chosen)
    install_log_record_factory()
    _disable_model_progress_bars()
    root = logging.root
    if not root.handlers:
        root.addHandler(build_log_handler(chosen))
    for handler in root.handlers:
        if isinstance(handler, _ProcessLogHandler):
            handler.setFormatter(formatter)
        _attach_redaction_filter(handler)
    root.setLevel(logging.INFO)
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


_PROGRESS_BARS_VARIABLE = "HF_HUB_DISABLE_PROGRESS_BARS"


def _disable_model_progress_bars() -> None:
    """PR 30: huggingface_hub's and transformers' tqdm bars ("Loading
    weights" as a model loads) write to stderr directly, not through
    logging, so they broke one JSON object per line in the workers' logs
    (observed in the E2E). sentence-transformers' own "Batches" bar is
    turned off at its calls instead (ingestion/embed.py, agents/knowledge/
    rerank.py), since it ignores this.

    No import: the models load lazily, inside a job (plan item 23), so
    neither library is imported yet when an entry point calls this. Setting
    huggingface_hub's own variable is enough: it reads it when imported, and
    transformers takes its setting from huggingface_hub when imported
    (transformers/utils/logging.py, 5.14). A default only, so an operator who
    sets the variable keeps their choice. If either library was imported
    first after all, its own switch is used as well."""
    os.environ.setdefault(_PROGRESS_BARS_VARIABLE, "1")
    hub = sys.modules.get("huggingface_hub.utils")
    if hub is not None and os.environ[_PROGRESS_BARS_VARIABLE] == "1":
        hub.disable_progress_bars()
    transformers_logging = sys.modules.get("transformers.utils.logging")
    if transformers_logging is not None and os.environ[_PROGRESS_BARS_VARIABLE] == "1":
        transformers_logging.disable_progress_bar()


# uvicorn's two loggers with handlers of their own (uvicorn/config.py's
# LOGGING_CONFIG); uvicorn.error has none and propagates to "uvicorn".
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access")


class _ProcessLogHandler(logging.StreamHandler[TextIO]):
    """The handler configure_logging() adds; its own class, so a second call
    can tell it from a handler someone else attached."""


def build_log_handler(log_format: str, stream: TextIO | None = None) -> logging.Handler:
    """A handler writing `log_format` lines to `stream` (stderr by default,
    as basicConfig did), with the redaction filter attached before the
    formatter runs. configure_logging() adds one to the root logger; the
    tests build their own on a StringIO."""
    handler = _ProcessLogHandler(stream if stream is not None else sys.stderr)
    _attach_redaction_filter(handler)
    handler.setFormatter(formatter_for(log_format))
    return handler


def _configured_log_format() -> str:
    # Imported here, not at the top: core/config.py imports this module, for
    # log_startup_refusal.
    from tenderos_backend.core.config import settings

    return settings.log_format


STARTUP_LOGGER = "tenderos.startup"


def log_startup_refusal(message: str, log_format: str) -> None:
    """Phase 4 item 5's refusals (core/config.py, tenderos_api/config.py)
    raise at import, before any entry point has configured logging, so they
    reached the operator only as a traceback. This configures logging and
    writes the refusal as one ERROR line, its whitespace collapsed so the
    multi-line production list stays one line in text mode too. The caller
    then re-raises, so the traceback and the exit code are unchanged. The
    messages name variables, never values (decision 0042)."""
    configure_logging(log_format)
    logging.getLogger(STARTUP_LOGGER).error("startup refused: %s", " ".join(message.split()))


@contextmanager
def startup_refusals_logged(log_format: str) -> Iterator[None]:
    """Run startup checks; a RuntimeError they raise is logged (above) and
    re-raised."""
    try:
        yield
    except RuntimeError as exc:
        log_startup_refusal(str(exc), log_format)
        raise
