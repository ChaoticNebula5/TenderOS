"""PR 34: migrate's pg_stat_statements step (tenderos_backend.db.query_stats).

It creates the extension only when the server preloads the library and keeps
no utility statement's text (track_utility off: migrate's reader step sends
ALTER ROLE ... PASSWORD), skips with one line when the library is not loaded
or the role may not create it, and lets any other error fail the step. A
stand-in connection records what was sent, so these run on any server (CI's
Postgres service does not preload it).
"""

import logging

import asyncpg
import pytest
from tenderos_backend.db import query_stats


class _Conn:
    def __init__(
        self,
        preload: str | None,
        track_utility: str | None = "off",
        create_error: Exception | None = None,
    ) -> None:
        self.preload = preload
        self.track_utility = track_utility
        self.create_error = create_error
        self.executed: list[str] = []

    async def fetchval(self, sql: str, *args):
        if "shared_preload_libraries" in sql:
            return self.preload
        if "track_utility" in sql:
            return self.track_utility
        assert args == ("pg_stat_statements",)
        return "1.10"

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if self.create_error is not None:
            raise self.create_error


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("pg_stat_statements", True),
        ("auto_explain, pg_stat_statements", True),
        ('"pg_stat_statements"', True),
        ("", False),
        ("auto_explain", False),
        ("pg_stat_statements_extra", False),
    ],
)
def test_the_preload_setting_is_read_as_a_list(setting, expected):
    assert query_stats.preloads(setting) is expected


@pytest.mark.asyncio
async def test_a_preloading_server_with_utility_tracking_off_gets_the_extension():
    conn = _Conn("pg_stat_statements")

    ready, line = await query_stats.ensure_query_stats(conn)

    assert ready is True
    assert conn.executed == ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements"]
    assert line == "pg_stat_statements ready (version 1.10)"


@pytest.mark.asyncio
@pytest.mark.parametrize("preload", ["", None])
async def test_a_server_that_does_not_preload_it_is_skipped(preload):
    conn = _Conn(preload)

    ready, line = await query_stats.ensure_query_stats(conn)

    assert ready is False
    assert conn.executed == []
    assert "skipped: the server does not preload it" in line


@pytest.mark.asyncio
@pytest.mark.parametrize("track_utility", ["on", None])
async def test_a_server_that_keeps_utility_statements_gets_no_extension(track_utility):
    conn = _Conn("pg_stat_statements", track_utility=track_utility)

    ready, line = await query_stats.ensure_query_stats(conn)

    assert ready is False
    assert conn.executed == []
    assert "not created: pg_stat_statements.track_utility is" in line
    assert "set it off" in line


@pytest.mark.asyncio
async def test_a_role_that_may_not_create_it_is_skipped():
    conn = _Conn("pg_stat_statements", create_error=asyncpg.InsufficientPrivilegeError("denied"))

    ready, line = await query_stats.ensure_query_stats(conn)

    assert ready is False
    assert "skipped: this role may not create it" in line


@pytest.mark.asyncio
async def test_any_other_error_fails_the_step():
    conn = _Conn("pg_stat_statements", create_error=asyncpg.UndefinedFileError("no control file"))

    with pytest.raises(asyncpg.UndefinedFileError):
        await query_stats.ensure_query_stats(conn)


@pytest.mark.parametrize(
    ("outcome", "level"),
    [
        ((True, "pg_stat_statements ready (version 1.10)"), logging.INFO),
        ((False, "pg_stat_statements skipped: the server does not preload it"), logging.INFO),
        (
            (False, "pg_stat_statements not created: pg_stat_statements.track_utility is on"),
            logging.WARNING,
        ),
        ((False, "pg_stat_statements skipped: this role may not create it"), logging.WARNING),
    ],
)
def test_main_logs_a_skip_a_host_should_see_as_a_warning(monkeypatch, caplog, outcome, level):
    async def fake_run(dsn: str):
        return outcome

    monkeypatch.setattr(query_stats, "_run", fake_run)
    monkeypatch.setattr("tenderos_backend.core.log_redaction.configure_logging", lambda: None)

    with caplog.at_level(logging.INFO, logger="tenderos_backend.db.query_stats"):
        query_stats.main()

    [record] = [r for r in caplog.records if r.name == "tenderos_backend.db.query_stats"]
    assert (record.levelno, record.getMessage()) == (level, outcome[1])
