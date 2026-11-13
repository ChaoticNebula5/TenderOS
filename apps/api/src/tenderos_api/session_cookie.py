"""The browser session's transport (plan item 34, option 2): the refresh token
in an HttpOnly cookie, and the checks that keep another site from using it.

The refresh token never appears in a response body or in page script. Login,
register and refresh set it; logout and a rejected refresh clear it. Every
other route still takes the bearer access token, unchanged.

The cookie (settings in config.py):
- HttpOnly: no script can read it.
- Secure: sent over HTTPS only (browsers treat http://localhost as secure).
  Production refuses AUTH_COOKIE_SECURE=false.
- SameSite=Strict: the browser leaves it off every request another site
  starts.
- Path AUTH_COOKIE_PATH (/api/auth): the browser's path, which nginx strips
  to the api's /auth. No other request carries it.
- No Domain: a host-only cookie.
- Max-Age: the seconds until the family expires, read from the token's own
  `exp`, so cookie and token end together. Rotation keeps the family's
  expiry, so each new cookie's Max-Age is shorter.

CSRF, on the four routes that set or use the cookie (login, register,
refresh, logout), as a route dependency:
- X-TenderOS-Client: web is required. A form post cannot set a header, and a
  cross-origin fetch that sets one needs a CORS preflight, which the api
  answers only for WEB_BASE_URL. It also covers a same-site page on another
  origin, which SameSite does not.
- An Origin header, when present, must be WEB_BASE_URL's origin. Browsers
  send Origin on every POST fetch; a tool that sends none is let through on
  the header alone.
Login and register are covered too, because they set the cookie: a forged
login would sign the victim into the attacker's account.
"""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import Request, Response
from tenderos_backend.core.security import decode_token

from tenderos_api.config import settings
from tenderos_api.errors import CsrfCheckFailedError

CLIENT_HEADER = "X-TenderOS-Client"
CLIENT_HEADER_VALUE = "web"


def _origin_of(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def require_web_client(request: Request) -> None:
    """Route dependency: 403 CSRF_CHECK_FAILED unless the request carries the
    client header and, when it sends an Origin, comes from WEB_BASE_URL.
    `Origin: null` (a sandboxed frame, some redirects) is a foreign origin."""
    if request.headers.get(CLIENT_HEADER) != CLIENT_HEADER_VALUE:
        raise CsrfCheckFailedError()
    origin = request.headers.get("Origin")
    if origin is not None and _origin_of(origin) != _origin_of(settings.web_base_url):
        raise CsrfCheckFailedError()


def read_refresh_cookie(request: Request) -> str | None:
    return request.cookies.get(settings.auth_cookie_name)


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    # The token was minted by this process a moment ago, so decoding it
    # cannot fail on signature or expiry; PyJWT writes exp as whole seconds.
    exp = decode_token(refresh_token)["exp"]
    if not isinstance(exp, int):
        raise TypeError("refresh token exp is not an integer")
    max_age = max(0, exp - int(datetime.now(UTC).timestamp()))
    response.set_cookie(
        settings.auth_cookie_name,
        refresh_token,
        max_age=max_age,
        path=settings.auth_cookie_path,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response) -> None:
    """Same name, path and attributes, Max-Age=0: the browser drops it."""
    response.delete_cookie(
        settings.auth_cookie_name,
        path=settings.auth_cookie_path,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )
