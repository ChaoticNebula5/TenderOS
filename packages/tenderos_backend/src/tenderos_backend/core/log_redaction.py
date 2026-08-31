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
"""

import logging
import re

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


def configure_logging() -> None:
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

    basicConfig()'s own std-library behavior is a no-op if the root logger
    already has a handler (true inside a pytest session, which attaches its
    own root-logger handlers per test) - so this is safe to call
    unconditionally at module-import time the same way install_log_
    redaction already is; it does not fight pytest's own log capture.

    That no-op case is exactly why the root logger's level is set as a
    second, unconditional step below rather than trusted to basicConfig()
    alone: basicConfig(level=...) only ever applies the level as part of
    its normal (non-no-op) path, so whenever a handler already exists -
    the pytest case above, or a real deployment where some other library
    attached one first - the root logger is left at its default WARNING.
    A Logger's effective level is checked before a LogRecord is even
    constructed (Logger.isEnabledFor, ahead of any handler or filter), so
    every .info() call in the codebase would be silently dropped at the
    logger itself in precisely the scenario this docstring already
    documents as "safe" - never reaching the redaction filter attached
    below, because it never reaches a handler at all. Setting the level
    directly, independent of whatever basicConfig() decided to do, is
    what actually closes the gap.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.root.setLevel(logging.INFO)
    for handler in logging.root.handlers:
        _attach_redaction_filter(handler)
