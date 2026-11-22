"""Observability phase, PR 30: the two log formats (LOG_FORMAT).

- `json`, the default: one JSON object per line, so `docker logs ... | jq`
  can filter one request or run (decision Q1 a: the standard library's own
  json, no dependency). Keys: `ts` (UTC, milliseconds), `level`, `logger`,
  `message`; then each id in the record's log context (log_context.py),
  present only when bound; then any `extra=` fields; then `exc` and `stack`
  when the record has them.
- `text`, for reading locally: the format every process used before,
  `%(asctime)s %(levelname)s %(name)s: %(message)s`, with the bound ids
  appended in brackets.

Neither redacts anything. Redaction is a filter on the same handler
(log_redaction.py), and a handler's filters run before its formatter, so
what reaches these is already redacted, the message, the traceback and every
extra field alike.
"""

import json
import logging
from datetime import UTC, datetime

from tenderos_backend.core.log_context import LOG_CONTEXT_FIELDS, RECORD_ATTRIBUTE

LOG_FORMATS = ("json", "text")
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Every attribute a plain LogRecord has, plus the two Formatter.format adds.
# Anything else on a record came from a caller's `extra=` (or from the log
# context factory, RECORD_ATTRIBUTE, handled on its own).
STANDARD_RECORD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"message", "asctime"}

# uvicorn adds a copy of some messages with terminal colour codes
# (uvicorn/server.py's "color_message"); the plain message is already there.
_DROPPED_EXTRAS = frozenset({"color_message"})

_JSON_KEYS = ("ts", "level", "logger", "message", *LOG_CONTEXT_FIELDS, "exc", "stack")


def record_extras(record: logging.LogRecord) -> dict[str, object]:
    """The record's `extra=` fields: everything a plain record does not have,
    without the log context and uvicorn's colour copy."""
    return {
        key: value
        for key, value in vars(record).items()
        if key not in STANDARD_RECORD_ATTRIBUTES
        and key != RECORD_ATTRIBUTE
        and key not in _DROPPED_EXTRAS
    }


def _context_of(record: logging.LogRecord) -> dict[str, str]:
    context = getattr(record, RECORD_ATTRIBUTE, None)
    if not isinstance(context, dict):
        return {}
    return {name: str(context[name]) for name in LOG_CONTEXT_FIELDS if name in context}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_context_of(record))
        for key, value in record_extras(record).items():
            # An extra named like one of this format's own keys is kept
            # under a prefix rather than overwriting it.
            payload[f"extra_{key}" if key in _JSON_KEYS else key] = value
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exc"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # default=str: an extra the redaction filter has not already turned
        # into a string (a handler without it) still serialises.
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(TEXT_FORMAT)

    def formatMessage(self, record: logging.LogRecord) -> str:
        line = super().formatMessage(record)
        context = _context_of(record)
        if not context:
            return line
        ids = " ".join(f"{name}={value}" for name, value in context.items())
        return f"{line} [{ids}]"


def formatter_for(log_format: str) -> logging.Formatter:
    if log_format == "json":
        return JsonFormatter()
    if log_format == "text":
        return TextFormatter()
    raise ValueError(f"LOG_FORMAT must be one of {', '.join(LOG_FORMATS)}, not {log_format!r}")
