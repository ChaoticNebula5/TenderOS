"""B15: RedactSensitiveDataFilter / install_log_redaction - defense-in-depth
redaction for the currently-unconfigured logging state (log_redaction.py's
own module docstring explains why lastResort specifically).

Deliberately does NOT use pytest's caplog fixture: caplog works by
attaching a real handler to the logger under test, and Logger.callHandlers
only ever falls through to logging.lastResort when NO handler exists
anywhere in the hierarchy - a caplog-instrumented test would never reach
the exact code path this module patches. Captures real stderr instead
(capsys), matching how this behavior was verified during B15 planning.

Two real test-environment gotchas found writing this file, not assumed:
1. pytest's own logging plugin attaches 4 handlers directly to the ROOT
   logger (confirmed by inspection - _LiveLoggingNullHandler, _FileHandler,
   2x LogCaptureHandler - present even with no caplog fixture requested and
   no explicit pytest logging config).
2. Clearing logging.root.handlers in a fixture's setup code does NOT
   survive into the test body: pytest's logging plugin wraps setup/call/
   teardown as three SEPARATE handler-attachment scopes
   (LoggingPlugin.pytest_runtest_setup/call/teardown, each its own
   catching_logs() context) - it re-attaches its own handler at the start
   of the "call" phase regardless of what a fixture did during "setup".
   Confirmed directly: a first attempt clearing handlers in the fixture
   still showed pytest's own "Captured log call" section on every
   assertion failure, meaning callHandlers never reached lastResort at
   all. _no_root_handlers below is applied inside each test's own body,
   wrapping only the actual logging call, which is the scope that
   actually matters.

logging.lastResort is a real, process-wide singleton - every test here
restores whatever it changed, so this file cannot leak state into
unrelated tests running in the same pytest session.
"""

import logging
from contextlib import contextmanager

import pytest
from tenderos_backend.core.log_redaction import RedactSensitiveDataFilter, install_log_redaction

_SECRET_VALUE = "input_value='CONFIDENTIAL DOCUMENT EXCERPT TEXT', input_type=str]"


@contextmanager
def _no_root_handlers():
    saved = logging.root.handlers
    logging.root.handlers = []
    try:
        yield
    finally:
        logging.root.handlers = saved


@pytest.fixture
def redaction_logger():
    """install_log_redaction itself is exercised (not just the Filter
    class directly), so this also proves the idempotent-attach behavior,
    not only the redaction logic alone."""
    logger = logging.getLogger(f"test_log_redaction.{id(object())}")
    install_log_redaction()
    yield logger
    logging.lastResort.filters = [
        f for f in logging.lastResort.filters if not isinstance(f, RedactSensitiveDataFilter)
    ]


def test_install_is_idempotent():
    install_log_redaction()
    install_log_redaction()
    install_log_redaction()
    count = sum(1 for f in logging.lastResort.filters if isinstance(f, RedactSensitiveDataFilter))
    assert count == 1
    logging.lastResort.filters = [
        f for f in logging.lastResort.filters if not isinstance(f, RedactSensitiveDataFilter)
    ]


def test_plain_message_with_the_pattern_is_redacted(redaction_logger, capsys):
    with _no_root_handlers():
        redaction_logger.warning("schema mismatch: %s", _SECRET_VALUE)

    captured = capsys.readouterr().err
    assert "CONFIDENTIAL DOCUMENT EXCERPT TEXT" not in captured
    assert "input_value=[REDACTED]" in captured


def test_exception_traceback_with_the_pattern_is_redacted(redaction_logger, capsys):
    with _no_root_handlers():
        try:
            raise ValueError(f"validation failed: {_SECRET_VALUE}")
        except ValueError:
            redaction_logger.exception("operation failed")

    captured = capsys.readouterr().err
    assert "CONFIDENTIAL DOCUMENT EXCERPT TEXT" not in captured
    assert "input_value=[REDACTED]" in captured
    # The redaction must not have suppressed the traceback entirely - only
    # the sensitive substring within it.
    assert "ValueError" in captured
    assert "operation failed" in captured


def test_info_level_still_reaches_nothing(redaction_logger, capsys):
    """Confirms this module doesn't accidentally change the separate,
    already-documented .info()-is-silently-dropped behavior (owner: B16,
    KNOWN_LIMITATIONS.md) - redaction only touches records that already
    reach a sink; it doesn't create a new one."""
    with _no_root_handlers():
        redaction_logger.info("this should still be dropped: %s", _SECRET_VALUE)

    captured = capsys.readouterr().err
    assert captured == ""


def test_message_with_no_sensitive_pattern_is_left_untouched(redaction_logger, capsys):
    with _no_root_handlers():
        redaction_logger.warning("ordinary message, nothing to redact here")

    captured = capsys.readouterr().err
    assert "ordinary message, nothing to redact here" in captured


def test_a_value_containing_an_embedded_decoy_marker_is_still_fully_redacted(
    redaction_logger, capsys
):
    """Regression: a naive non-greedy `input_value=.*?, input_type=\\w+]`
    stops at the FIRST such substring, including one embedded inside the
    sensitive value's own text rather than pydantic's real closing marker -
    leaking everything between the fake early stop and the real end. The
    pattern must extend to the LAST occurrence in the record, not the
    first."""
    decoy_secret = "input_value='fake, input_type=str] REAL_SECRET_AFTER_DECOY', input_type=str]"
    with _no_root_handlers():
        redaction_logger.warning("schema mismatch: %s", decoy_secret)

    captured = capsys.readouterr().err
    assert "REAL_SECRET_AFTER_DECOY" not in captured
    assert "input_value=[REDACTED]" in captured


def test_dict_style_args_are_redacted_by_value_not_replaced_with_keys(redaction_logger, capsys):
    """LogRecord.args can be a single Mapping, not only a tuple - Python's
    own logging module stores it unwrapped for %(name)s-style format
    strings. The old unconditional `tuple(... for arg in record.args)`
    iterated dict KEYS (dropping every value) and replaced record.args with
    the wrong type entirely, breaking getMessage()'s own `msg % args`."""
    with _no_root_handlers():
        redaction_logger.warning("schema mismatch: %(detail)s", {"detail": _SECRET_VALUE})

    captured = capsys.readouterr().err
    assert "CONFIDENTIAL DOCUMENT EXCERPT TEXT" not in captured
    assert "input_value=[REDACTED]" in captured
