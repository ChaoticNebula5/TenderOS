"""Phase 4 item 5 (decision 0042): the settings checks that run at import
time, right after each `settings = Settings()`. Tested directly with
constructed settings, one failing and one passing case per rule, rather
than by setting real environment variables and re-importing the modules.

`Settings.model_construct` builds a settings object without reading the
environment or .env, so these tests never see the developer's real values.
"""

import pytest
from tenderos_api.config import Settings as ApiSettings
from tenderos_api.config import _api_production_problems
from tenderos_backend.core.config import (
    JWT_SIGNING_KEY_MIN_BYTES,
    Settings,
    _production_problems,
    _require_jwt_signing_key_length,
    reject_production_problems,
)

_GOOD_KEY = "k" * JWT_SIGNING_KEY_MIN_BYTES
# Any value that is not a placeholder; nothing here is a real credential.
_SET = "set-for-this-test"


def _dsn(password_part: str) -> str:
    return f"postgresql+asyncpg://tenderos{password_part}@postgres:5432/tenderos"


def _backend(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "postgres_dsn": _dsn(f":{_SET}"),
        "redis_url": "redis://redis:6379/0",
        "jwt_signing_key": _GOOD_KEY,
        "s3_endpoint_url": "http://s3:9000",
        "s3_access_key": _SET,
        "s3_secret_key": _SET,
        "s3_bucket": "tenderos",
        "neo4j_uri": "bolt://neo4j:7687",
        "neo4j_user": "neo4j",
        "neo4j_password": _SET,
        "extractor_model": "model-a",
        "verifier_model": "model-b",
        "reasoning_model": "model-c",
    }
    values.update(overrides)
    return Settings.model_construct(**values)


# The API's settings hold connection fields its production checks never read.
_NOT_READ = "not-read-by-these-checks"


def _api(**overrides: object) -> ApiSettings:
    values: dict[str, object] = {
        "app_env": "production",
        "postgres_dsn": _NOT_READ,
        "redis_url": _NOT_READ,
        "neo4j_uri": _NOT_READ,
        "neo4j_user": _NOT_READ,
        "neo4j_password": _NOT_READ,
        "s3_endpoint_url": _NOT_READ,
        "web_base_url": "https://tenders.example.com",
        "rate_limit_enabled": True,
        "e2e_rate_limit_bypass_enabled": False,
    }
    values.update(overrides)
    return ApiSettings.model_construct(**values)


# --- the JWT key length, every environment ---------------------------------


def test_a_key_of_the_minimum_length_is_accepted():
    _require_jwt_signing_key_length(_GOOD_KEY)


def test_a_short_key_is_refused_naming_the_variable_not_the_value():
    short = "s3cr3t-8"
    with pytest.raises(RuntimeError, match="JWT_SIGNING_KEY") as raised:
        _require_jwt_signing_key_length(short)
    assert short not in str(raised.value)


def test_the_minimum_is_counted_in_bytes_not_characters():
    # 16 characters, 32 bytes in UTF-8: what HS256 (and pyjwt) measure.
    _require_jwt_signing_key_length("é" * 16)
    # 31 characters and 31 bytes: one byte short.
    with pytest.raises(RuntimeError, match="JWT_SIGNING_KEY"):
        _require_jwt_signing_key_length("k" * 31)


# --- backend settings in production ------------------------------------------


def test_good_production_settings_have_no_problems():
    assert _production_problems(_backend()) == []


def test_outside_production_nothing_is_checked():
    assert _production_problems(_backend(app_env="local", extractor_model="changeme")) == []


@pytest.mark.parametrize("name", ["extractor_model", "verifier_model", "reasoning_model"])
def test_a_placeholder_model_name_is_a_problem(name: str):
    problems = _production_problems(_backend(**{name: "changeme"}))
    assert problems == [f"{name.upper()} is unset or the placeholder 'changeme'"]


@pytest.mark.parametrize("name", ["s3_access_key", "s3_secret_key", "neo4j_password"])
@pytest.mark.parametrize("value", ["changeme", ""])
def test_a_placeholder_credential_is_a_problem(name: str, value: str):
    problems = _production_problems(_backend(**{name: value}))
    assert problems == [f"{name.upper()} is unset or the placeholder 'changeme'"]


@pytest.mark.parametrize("password_part", [":changeme", ""])
def test_a_placeholder_or_missing_database_password_is_a_problem(password_part: str):
    assert _production_problems(_backend(postgres_dsn=_dsn(password_part))) == [
        "POSTGRES_DSN has no password or the placeholder 'changeme'"
    ]


def test_problems_are_reported_together_without_values():
    problems = _production_problems(_backend(s3_secret_key="changeme", reasoning_model="changeme"))
    with pytest.raises(RuntimeError) as raised:
        reject_production_problems(problems)
    message = str(raised.value)
    assert "REASONING_MODEL" in message and "S3_SECRET_KEY" in message
    assert _SET not in message


def test_no_problems_raises_nothing():
    reject_production_problems([])


# --- API settings in production ----------------------------------------------


def test_good_api_production_settings_have_no_problems():
    assert _api_production_problems(_api()) == []


def test_api_outside_production_nothing_is_checked():
    assert (
        _api_production_problems(_api(app_env="local", web_base_url="http://localhost:5173")) == []
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "https://localhost:5173",
        "https://127.0.0.1",
        "http://tenders.example.com",
    ],
)
def test_a_local_or_plain_http_cors_origin_is_a_problem(origin: str):
    assert _api_production_problems(_api(web_base_url=origin)) == [
        "WEB_BASE_URL (the CORS origin) must be an https URL that is not localhost"
    ]


def test_rate_limiting_off_is_a_problem():
    assert _api_production_problems(_api(rate_limit_enabled=False)) == [
        "RATE_LIMIT_ENABLED must be true"
    ]


def test_the_e2e_bypass_on_is_a_problem():
    assert _api_production_problems(_api(e2e_rate_limit_bypass_enabled=True)) == [
        "E2E_RATE_LIMIT_BYPASS_ENABLED must be false"
    ]


def test_the_refresh_cookie_without_secure_is_a_problem():
    """Plan item 34, option 2: the refresh token's cookie must be Secure in
    production, so it never crosses plain HTTP."""
    assert _api_production_problems(_api(auth_cookie_secure=False)) == [
        "AUTH_COOKIE_SECURE must be true"
    ]
