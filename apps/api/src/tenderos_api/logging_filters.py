"""Phase 4 plan item 3: keep SSE tickets out of the access log.

GET .../events/stream carries its single-use ticket in the query string,
because EventSource cannot send headers (sse_tickets.py, decision 0008).
uvicorn writes every request's path and query string to the uvicorn.access
logger, so each ticket was written to the log. A ticket is consumed on
first use and lives 30 s, so a logged one is normally dead already; this
filter removes it anyway, so the log never holds a credential, even a
spent one.

A separate module rather than part of tenderos_backend.core.log_redaction:
that module redacts the shared backend's own records and is installed by
the worker processes too, while uvicorn's access log exists only in the api
process. main.py calls install_access_log_redaction() at import.

Why that is late enough (read in uvicorn 0.54.0's config.py): uvicorn
applies its logging config in Config.__init__, before Config.load()
imports the app, so this filter is attached after uvicorn's own setup.
It is attached to the uvicorn.access LOGGER, not to a handler: uvicorn's
access handler does not propagate to the root handlers that
configure_logging() filters, and a logger's filters survive a later
dictConfig, which replaces handlers but leaves existing filters in place
(tests/unit/test_logging_filters.py checks both orders).

The proxy in front of the api, once there is one, keeps its own access
log; that needs the same redaction at deployment (plan item 3, host part).
"""

import logging
import re

ACCESS_LOGGER_NAME = "uvicorn.access"
REDACTED_TICKET = "ticket=[redacted]"

# `ticket=` as a query parameter: after `?` or `&`, up to the next `&`,
# whitespace or quote. The value is secrets.token_urlsafe (letters, digits,
# `-` and `_`), but anything up to the delimiter is replaced, so a ticket
# sent percent-encoded or malformed is removed too.
_TICKET_PARAMETER = re.compile(r"(?<=[?&])ticket=[^&\s\"]*")


def redact_tickets(text: str) -> str:
    return _TICKET_PARAMETER.sub(REDACTED_TICKET, text)


class RedactSseTicketFilter(logging.Filter):
    """Replaces every ticket value in an access-log record with [redacted].

    uvicorn passes the path and query string as one of the record's args
    ('%s - "%s %s HTTP/%s" %d'), so the args are rewritten, not only the
    format string; the message is rewritten too in case a caller logged
    an already formatted line.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_tickets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_tickets(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


def install_access_log_redaction() -> None:
    """Attach the filter to uvicorn's access logger, once."""
    access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
    if any(isinstance(f, RedactSseTicketFilter) for f in access_logger.filters):
        return
    access_logger.addFilter(RedactSseTicketFilter())
