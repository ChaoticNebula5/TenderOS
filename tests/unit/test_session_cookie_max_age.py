"""PR 34: the refresh cookie's Max-Age is rounded down
(apps/api/src/tenderos_api/session_cookie.py, set_refresh_cookie), so a
cookie never outlives its family.

Before, Max-Age was `exp - int(now)`, which dropped now's fraction and could
exceed the time left by up to a second; the route test
test_the_cookies_max_age_follows_the_familys_expiry_not_a_fresh_7_days failed
once on it (3601) when the Postgres clock ran 0.6 s ahead of the host's.
Here now and exp are pinned, so the rounding is the only thing measured.
"""

from datetime import UTC, datetime

import pytest
from fastapi import Response
from tenderos_api import session_cookie

_FRACTIONAL_NOW = datetime(2026, 11, 25, 8, 0, 0, 600_000, tzinfo=UTC)  # .6 s past
_WHOLE_NOW = _FRACTIONAL_NOW.replace(microsecond=0)


def _max_age(monkeypatch, now: datetime, exp: int) -> str:
    class _Clock:
        @staticmethod
        def now(tz=None):
            return now

    monkeypatch.setattr(session_cookie, "datetime", _Clock)
    monkeypatch.setattr(session_cookie, "decode_token", lambda token: {"exp": exp})
    response = Response()
    session_cookie.set_refresh_cookie(response, "fake-token")
    header = response.headers["set-cookie"]
    return header.split("Max-Age=", 1)[1].split(";", 1)[0]


def test_max_age_is_rounded_down_so_the_cookie_never_outlives_its_family(monkeypatch):
    exp = int(_WHOLE_NOW.timestamp()) + 3600
    # 3,599.4 s left: Max-Age 3599, never 3600.
    assert _max_age(monkeypatch, _FRACTIONAL_NOW, exp) == "3599"


def test_max_age_is_exact_when_now_is_on_the_second(monkeypatch):
    exp = int(_WHOLE_NOW.timestamp()) + 3600
    assert _max_age(monkeypatch, _WHOLE_NOW, exp) == "3600"


@pytest.mark.parametrize("seconds_left", [0, -5])
def test_a_family_with_no_time_left_gets_max_age_0(monkeypatch, seconds_left):
    exp = int(_WHOLE_NOW.timestamp()) + seconds_left
    assert _max_age(monkeypatch, _FRACTIONAL_NOW, exp) == "0"
