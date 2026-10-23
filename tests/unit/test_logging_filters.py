"""Phase 4 plan item 3: the access-log filter that removes SSE tickets.

The record is made the way uvicorn 0.54.0 makes it (h11_impl.py and
httptools_impl.py: the same format string and args, the path from
uvicorn's own get_path_with_query_string) and written through uvicorn's own
LOGGING_CONFIG handler and AccessFormatter, so the asserted text is the
line uvicorn would print.
"""

import copy
import logging
import logging.config
import secrets

import pytest
from tenderos_api.logging_filters import (
    ACCESS_LOGGER_NAME,
    RedactSseTicketFilter,
    install_access_log_redaction,
    redact_tickets,
)
from uvicorn.config import LOGGING_CONFIG
from uvicorn.protocols.utils import get_path_with_query_string

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def _restore_uvicorn_loggers():
    """dictConfig changes process-wide logger state; put it back."""
    saved = {
        name: (lg.handlers[:], lg.filters[:], lg.level, lg.propagate)
        for name, lg in ((n, logging.getLogger(n)) for n in _UVICORN_LOGGERS)
    }
    yield
    for name, (handlers, filters, level, propagate) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.filters[:] = filters
        lg.setLevel(level)
        lg.propagate = propagate


def _configure_uvicorn_logging() -> None:
    """What uvicorn's Config.__init__ does with the default log config."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config["formatters"]["access"]["use_colors"] = False
    logging.config.dictConfig(config)


def _log_access_line(query_string: bytes) -> None:
    scope = {
        "type": "http",
        "path": "/tenders/7f0c/workflow-runs/9a1d/events/stream",
        "query_string": query_string,
    }
    logging.getLogger(ACCESS_LOGGER_NAME).info(
        '%s - "%s %s HTTP/%s" %d',
        "172.18.0.1:51234",
        "GET",
        get_path_with_query_string(scope),  # type: ignore[arg-type]
        "1.1",
        200,
    )


def test_the_ticket_is_redacted_from_a_real_access_line(capsys):
    ticket = secrets.token_urlsafe(32)
    _configure_uvicorn_logging()
    install_access_log_redaction()

    _log_access_line(f"ticket={ticket}".encode())

    line = capsys.readouterr().out
    assert "/events/stream?ticket=[redacted] HTTP/1.1" in line
    assert ticket not in line


def test_the_filter_survives_uvicorn_configuring_logging_again(capsys):
    """uvicorn configures logging before importing the app (the real
    order, covered above). This is the other order: installed first, then
    uvicorn's dictConfig, which replaces the logger's handlers."""
    ticket = secrets.token_urlsafe(32)
    install_access_log_redaction()
    _configure_uvicorn_logging()

    _log_access_line(f"ticket={ticket}".encode())

    line = capsys.readouterr().out
    assert "ticket=[redacted]" in line
    assert ticket not in line


def test_installing_twice_attaches_one_filter():
    install_access_log_redaction()
    install_access_log_redaction()
    filters = logging.getLogger(ACCESS_LOGGER_NAME).filters
    assert sum(isinstance(f, RedactSseTicketFilter) for f in filters) == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/s?ticket=abc-_123", "/s?ticket=[redacted]"),
        ("/s?a=1&ticket=abc&b=2", "/s?a=1&ticket=[redacted]&b=2"),
        ("/s?ticket=abc%2Fdef", "/s?ticket=[redacted]"),
        ("/s?ticket=", "/s?ticket=[redacted]"),
        ('"GET /s?ticket=abc HTTP/1.1" 200', '"GET /s?ticket=[redacted] HTTP/1.1" 200'),
        # Only the parameter named ticket: not a longer name ending in it,
        # and not the word in a path.
        ("/s?subticket=abc", "/s?subticket=abc"),
        ("/tickets/ticket=abc", "/tickets/ticket=abc"),
    ],
)
def test_redact_tickets(text: str, expected: str):
    assert redact_tickets(text) == expected
