"""PR 30: the JSON and text formats (core/log_format.py), the redaction that
must still apply in JSON, to the message and to every extra field, the SSE
ticket filter in JSON, configure_logging() itself (LOG_FORMAT, its own
handler, uvicorn's loggers), and startup refusals logged as one ERROR line.

The handler tests build the same handler configure_logging() adds
(build_log_handler) on a StringIO and log through an isolated logger, so
pytest's own root handlers never see or change the output."""

import io
import json
import logging
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url
from tenderos_api.logging_filters import RedactSseTicketFilter
from tenderos_backend.core import config as backend_config
from tenderos_backend.core.log_context import install_log_record_factory, log_context
from tenderos_backend.core.log_format import formatter_for
from tenderos_backend.core.log_redaction import (
    STARTUP_LOGGER,
    RedactSensitiveDataFilter,
    build_log_handler,
    configure_logging,
)

_SECRET = "input_value='CONFIDENTIAL DOCUMENT EXCERPT TEXT', input_type=str]"
_REQUEST_ID = "cd" * 16  # the W3C shape, low-entropy so it reads as no secret
# Not a placeholder, so a production check passes on it; never a credential.
_FILLER = "set-for-this-test"


def _logger_writing(log_format: str) -> tuple[logging.Logger, io.StringIO]:
    install_log_record_factory()
    stream = io.StringIO()
    logger = logging.Logger(f"pr30.{uuid.uuid4().hex[:8]}", logging.DEBUG)
    logger.addHandler(build_log_handler(log_format, stream))
    return logger, stream


def _json_lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_a_json_line_has_the_record_and_its_bound_ids():
    logger, stream = _logger_writing("json")
    run_id = uuid.uuid4()
    with log_context(request_id=_REQUEST_ID, run_id=run_id, job_id="op_1"):
        logger.info("run %s started", "r1", extra={"attempt": 2})
    [line] = _json_lines(stream)
    assert line.pop("ts").endswith("+00:00")
    assert line == {
        "level": "INFO",
        "logger": logger.name,
        "message": "run r1 started",
        "request_id": _REQUEST_ID,
        "run_id": str(run_id),
        "job_id": "op_1",
        "attempt": 2,
    }


def test_an_unbound_id_is_absent_not_null():
    logger, stream = _logger_writing("json")
    logger.warning("nothing bound")
    [line] = _json_lines(stream)
    assert set(line) == {"ts", "level", "logger", "message"}


def test_an_extra_named_like_a_key_of_the_format_is_kept_under_a_prefix():
    logger, stream = _logger_writing("json")
    with log_context(request_id=_REQUEST_ID):
        logger.info("x", extra={"level": "mine", "request_id": "from the caller"})
    [line] = _json_lines(stream)
    assert line["level"] == "INFO"
    assert line["request_id"] == _REQUEST_ID
    assert line["extra_level"] == "mine"
    assert line["extra_request_id"] == "from the caller"


def test_the_traceback_is_one_field_of_the_same_line():
    logger, stream = _logger_writing("json")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed")
    [line] = _json_lines(stream)
    assert line["message"] == "failed"
    assert "Traceback (most recent call last)" in line["exc"]
    assert "ValueError: boom" in line["exc"]


def test_uvicorns_colour_copy_of_a_message_is_left_out():
    logger, stream = _logger_writing("json")
    logger.info("Started server process", extra={"color_message": "\x1b[32mStarted\x1b[0m"})
    [line] = _json_lines(stream)
    assert "color_message" not in line


@pytest.mark.parametrize("log_format", ["json", "text"])
def test_redaction_still_applies_to_the_message_and_its_args(log_format):
    logger, stream = _logger_writing(log_format)
    logger.warning("schema mismatch: %s", _SECRET)
    logger.warning(f"already formatted: {_SECRET}")
    output = stream.getvalue()
    assert "CONFIDENTIAL" not in output
    assert output.count("input_value=[REDACTED]") == 2


def test_redaction_applies_to_every_extra_field_in_json():
    class Holder:
        def __str__(self) -> str:
            return f"holder({_SECRET})"

    logger, stream = _logger_writing("json")
    logger.warning(
        "x",
        extra={
            "detail": _SECRET,
            "nested": {"inner": [_SECRET, {"deeper": _SECRET}]},
            "object": Holder(),
        },
    )
    raw = stream.getvalue()
    assert "CONFIDENTIAL" not in raw
    [line] = _json_lines(stream)
    assert line["detail"] == "input_value=[REDACTED]"
    assert line["nested"] == {
        "inner": ["input_value=[REDACTED]", {"deeper": "input_value=[REDACTED]"}]
    }
    assert line["object"] == "holder(input_value=[REDACTED])"


def test_redaction_applies_to_the_traceback_in_json():
    logger, stream = _logger_writing("json")
    try:
        raise ValueError(f"validation failed: {_SECRET}")
    except ValueError:
        logger.exception("failed")
    assert "CONFIDENTIAL" not in stream.getvalue()
    [line] = _json_lines(stream)
    assert "input_value=[REDACTED]" in line["exc"]


def test_the_sse_ticket_filter_applies_in_json_to_the_message_and_extras():
    logger, stream = _logger_writing("json")
    logger.addFilter(RedactSseTicketFilter())
    logger.info(
        '%s - "%s %s HTTP/%s" %d',
        "172.18.0.1:5000",
        "GET",
        "/workflow-runs/r/events/stream?ticket=SECRETTICKET&x=1",
        "1.1",
        200,
        extra={"query": "?ticket=SECRETEXTRA"},
    )
    assert "SECRET" not in stream.getvalue()
    [line] = _json_lines(stream)
    assert "ticket=[redacted]&x=1" in line["message"]
    assert line["query"] == "?ticket=[redacted]"


def test_text_mode_keeps_the_plain_format_and_appends_the_ids():
    logger, stream = _logger_writing("text")
    logger.info("no ids")
    with log_context(request_id=_REQUEST_ID, job_id="op_1"):
        logger.info("with ids")
    first, second = stream.getvalue().splitlines()
    assert first.endswith(f" INFO {logger.name}: no ids")
    assert second.endswith(f" INFO {logger.name}: with ids [request_id={_REQUEST_ID} job_id=op_1]")
    with pytest.raises(json.JSONDecodeError):
        json.loads(first)


def test_text_mode_puts_the_ids_before_the_traceback():
    logger, stream = _logger_writing("text")
    with log_context(job_id="op_1"):
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("failed")
    lines = stream.getvalue().splitlines()
    assert lines[0].endswith("failed [job_id=op_1]")
    assert lines[1] == "Traceback (most recent call last):"


def test_an_unknown_format_is_refused():
    with pytest.raises(ValueError, match="LOG_FORMAT"):
        formatter_for("xml")


def test_log_format_is_json_by_default_and_checked():
    assert backend_config.Settings.model_fields["log_format"].default == "json"
    with pytest.raises(ValidationError, match="log_format"):
        backend_config.Settings(_env_file=None, log_format="xml")


@contextmanager
def _bare_logging():
    """A root logger with no handlers and uvicorn's loggers as uvicorn
    leaves them, for the length of the block; restored after. Applied
    inside the test body: pytest re-attaches its root handlers per phase
    (test_log_redaction.py's module docstring)."""
    saved_root = logging.root.handlers
    saved_level = logging.root.level
    uvicorn_loggers = [logging.getLogger(n) for n in ("uvicorn", "uvicorn.access")]
    saved_uvicorn = [(lg.handlers, lg.propagate) for lg in uvicorn_loggers]
    logging.root.handlers = []
    for lg in uvicorn_loggers:
        lg.handlers = [logging.StreamHandler(io.StringIO())]
        lg.propagate = False
    try:
        yield
    finally:
        logging.root.handlers = saved_root
        logging.root.setLevel(saved_level)
        for lg, (handlers, propagate) in zip(uvicorn_loggers, saved_uvicorn, strict=True):
            lg.handlers = handlers
            lg.propagate = propagate


def test_configure_logging_adds_one_handler_once_and_resets_its_format(capsys):
    with _bare_logging():
        configure_logging("json")
        configure_logging("json")
        assert len(logging.root.handlers) == 1
        logging.getLogger("pr30.configure").info("as json")
        configure_logging("text")
        assert len(logging.root.handlers) == 1
        logging.getLogger("pr30.configure").info("as text")
        handler = logging.root.handlers[0]
        assert any(isinstance(f, RedactSensitiveDataFilter) for f in handler.filters)
    first, second = capsys.readouterr().err.splitlines()
    assert json.loads(first)["message"] == "as json"
    assert second.endswith("INFO pr30.configure: as text")


def test_configure_logging_reads_log_format_from_the_settings(capsys, monkeypatch):
    monkeypatch.setattr(backend_config.settings, "log_format", "text")
    with _bare_logging():
        configure_logging()
        logging.getLogger("pr30.settings").info("from settings")
    assert capsys.readouterr().err.strip().endswith("INFO pr30.settings: from settings")


def test_uvicorns_loggers_go_through_the_root_handler(capsys):
    with _bare_logging():
        configure_logging("json")
        access = logging.getLogger("uvicorn.access")
        assert access.handlers == [] and access.propagate is True
        assert logging.getLogger("uvicorn").handlers == []
        assert access.hasHandlers()  # what uvicorn checks before writing an access line
        with log_context(request_id=_REQUEST_ID):
            access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/health/live", "1.1", 200)
    [line] = [json.loads(raw) for raw in capsys.readouterr().err.splitlines()]
    assert line["logger"] == "uvicorn.access"
    assert line["message"] == '1.2.3.4:5 - "GET /health/live HTTP/1.1" 200'
    assert line["request_id"] == _REQUEST_ID


def _startup_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == STARTUP_LOGGER]


def test_a_startup_refusal_is_one_error_line_then_raised(caplog):
    refused = backend_config.settings.model_copy(
        update={"app_env": "production", "llm_provider": "fake"}
    )
    with caplog.at_level(logging.ERROR, logger=STARTUP_LOGGER):
        with pytest.raises(RuntimeError, match="LLM_PROVIDER=fake"):
            backend_config.run_startup_checks(refused)
    [record] = _startup_records(caplog)
    assert record.levelno == logging.ERROR
    assert record.getMessage().startswith("startup refused: LLM_PROVIDER=fake is not permitted")


def test_a_multi_line_refusal_is_logged_on_one_line(caplog):
    refused = backend_config.settings.model_copy(
        update={"app_env": "production", "llm_provider": "gemini", "extractor_model": "changeme"}
    )
    with caplog.at_level(logging.ERROR, logger=STARTUP_LOGGER):
        with pytest.raises(RuntimeError, match="Refusing to start"):
            backend_config.run_startup_checks(refused)
    [record] = _startup_records(caplog)
    message = record.getMessage()
    assert "\n" not in message
    assert "EXTRACTOR_MODEL is unset or the placeholder 'changeme'" in message


def test_settings_that_pass_log_nothing(caplog):
    with caplog.at_level(logging.ERROR, logger=STARTUP_LOGGER):
        backend_config.run_startup_checks(backend_config.settings)
    assert _startup_records(caplog) == []


# Settings this test run may take from a .env file rather than the
# environment; the child process gets fillers, never the real values (an
# assertion that fails prints the child's stderr).
_REQUIRED_FROM_ENV_FILE = (
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
)


def _import_in_a_fresh_process(module: str, tmp_path, **overrides: str):
    """Import `module` in a new interpreter, with this test run's
    environment plus `overrides`, in an empty directory so no .env file is
    read (its settings are filled in, above)."""
    env = {
        **os.environ,
        **dict.fromkeys(_REQUIRED_FROM_ENV_FILE, _FILLER),
        "JWT_SIGNING_KEY": _FILLER * 3,  # over the 32 bytes the check needs
        "LOG_FORMAT": "json",
        **overrides,
    }
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _error_lines(stderr: str) -> list[dict]:
    lines = []
    for raw in stderr.splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(line, dict) and line.get("level") == "ERROR":
            lines.append(line)
    return lines


def test_a_process_refused_at_import_writes_one_json_error_line_before_the_traceback(tmp_path):
    result = _import_in_a_fresh_process(
        "tenderos_backend.core.config", tmp_path, APP_ENV="production", LLM_PROVIDER="fake"
    )
    assert result.returncode != 0
    lines = _error_lines(result.stderr)
    assert len(lines) == 1, result.stderr
    [line] = lines
    assert line["logger"] == STARTUP_LOGGER
    assert line["message"].startswith("startup refused: LLM_PROVIDER=fake is not permitted")
    assert result.stderr.index(line["message"]) < result.stderr.index("Traceback")
    assert "RuntimeError: LLM_PROVIDER=fake is not permitted" in result.stderr


def test_the_apis_own_refusal_is_logged_too(tmp_path):
    """Every backend check passes (no placeholder anywhere), so the refusal
    is the api's own: a localhost CORS origin in production."""
    filled = dict.fromkeys(("EXTRACTOR_MODEL", "VERIFIER_MODEL", "REASONING_MODEL"), _FILLER)
    dsn = make_url(os.environ["POSTGRES_DSN"]).set(password=_FILLER)
    result = _import_in_a_fresh_process(
        "tenderos_api.config",
        tmp_path,
        APP_ENV="production",
        LLM_PROVIDER="gemini",
        POSTGRES_DSN=dsn.render_as_string(hide_password=False),
        WEB_BASE_URL="http://localhost:5173",
        **filled,
    )
    assert result.returncode != 0, result.stderr
    lines = _error_lines(result.stderr)
    assert len(lines) == 1, result.stderr
    [line] = lines
    assert line["message"].startswith("startup refused: Refusing to start: APP_ENV=production")
    assert "WEB_BASE_URL (the CORS origin)" in line["message"]
