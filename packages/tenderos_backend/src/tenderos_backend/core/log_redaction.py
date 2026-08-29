"""B15: log redaction, as defense-in-depth against the currently-unconfigured
logging state - not the primary fix for the one concretely identified leak.

apps/api and apps/worker call logging.basicConfig() nowhere. Verified
directly (a throwaway script during B15 planning, not assumed): every
.exception()/.warning()/.error() call in this codebase still surfaces, via
Python's own "handler of last resort" (logging.lastResort), even with zero
handlers configured anywhere. .info() calls are silently dropped before a
LogRecord is even constructed - that class of gap needs real logging
configuration to exist before it can be verified at all (owner: B16, see
KNOWN_LIMITATIONS.md); nothing here changes that.

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
"""

import logging
import re

# Anchored on `, input_type=<identifier>]` - the one part of pydantic's
# ValidationError formatting that's always plain, predictable text,
# regardless of what the offending value itself contains (including a
# literal `]`, which a naive "match up to the first `]`" pattern would
# truncate on too early, leaving the rest of the raw value unredacted).
_INPUT_VALUE_PATTERN = re.compile(r"input_value=.*?, input_type=\w+\]", re.DOTALL)
_REDACTED = "input_value=[REDACTED]"


def _redact_text(text: str) -> str:
    return _INPUT_VALUE_PATTERN.sub(_REDACTED, text)


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
            record.args = tuple(
                _redact_text(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        if record.exc_info and not record.exc_text:
            record.exc_text = _redact_text(logging.Formatter().formatException(record.exc_info))
        return True


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

    Real, named coupling to the current no-basicConfig state, not
    something this function can outgrow silently: once real logging
    configuration exists anywhere in the hierarchy (basicConfig, or any
    handler added to any logger), Logger.callHandlers stops falling
    through to lastResort at all, and this filter's redaction stops
    applying with no error, no warning, nothing to notice at the moment
    it happens. Whoever builds that (owner: B16, per KNOWN_LIMITATIONS.md)
    needs to re-attach RedactSensitiveDataFilter to whatever handler(s)
    they add - this function's own call sites do not automatically cover
    that; it is not a general "logging is now safe forever" guarantee.
    """
    handler = logging.lastResort
    if handler is None:
        # Typed Handler | None upstream (a caller could set it to None) -
        # not something this project's own processes ever do, but mypy
        # correctly won't assume that. Nothing to attach a filter to.
        return
    if any(isinstance(f, RedactSensitiveDataFilter) for f in handler.filters):
        return
    handler.addFilter(RedactSensitiveDataFilter())
