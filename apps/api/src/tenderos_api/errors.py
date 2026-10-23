from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        # Response headers for this error, e.g. Retry-After. Sent by the
        # exception handler below; None for almost every error.
        self.headers = headers


class IdempotencyConflictError(APIError):
    def __init__(self) -> None:
        super().__init__(
            409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key reused with a different request body."
        )


class IdempotencyInProgressError(APIError):
    # Phase 4 blocker 2: same key, same request, and the first attempt still
    # holds a live lease on it. Not a conflict: waiting and retrying with the
    # same key is exactly right, so Retry-After carries the seconds left on
    # that lease (computed in SQL, IdempotencyRepository.
    # lease_seconds_remaining).
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            409,
            "IDEMPOTENCY_IN_PROGRESS",
            "This request is already being processed. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after_seconds)},
        )


class OrgAccessDeniedError(APIError):
    def __init__(self) -> None:
        super().__init__(403, "ORG_ACCESS_DENIED", "Not a member of this organization.")


class ReadinessNotWaivedError(APIError):
    # B12: routes/gates.py's own enforcement of "refuses approval until
    # resolved or explicitly waived by authorized actor" (Build Guide
    # stop-gate, verbatim) - decide_gate raises this instead of ever
    # calling gate_service.record_decision when a final_readiness gate's
    # "approve" arrives with outstanding blockers and no waive:true.
    # RBAC already restricts who can reach this route at all for this gate
    # type (final_readiness_approval is APPROVER/ADMIN-only) - "authorized
    # actor" is enforced there, not here.
    def __init__(self) -> None:
        super().__init__(
            409,
            "READINESS_NOT_WAIVED",
            "Readiness has unresolved blockers. Resolve them, or resubmit "
            "with waive=true to approve anyway.",
        )


class TargetOwnershipDeniedError(APIError):
    # B12: routes/gates.py's own translation of gate_target_service.
    # TargetOwnershipError - same 403 shape as OrgAccessDeniedError, since
    # both are "the caller is authenticated, but the specific resource
    # they're trying to touch isn't theirs to touch."
    def __init__(self) -> None:
        super().__init__(
            403,
            "TARGET_OWNERSHIP_DENIED",
            "One or more target_updates entries do not belong to this gate's tender.",
        )


class UserNotFoundError(APIError):
    def __init__(self) -> None:
        super().__init__(404, "USER_NOT_FOUND", "No user is registered with that email.")


class AlreadyMemberError(APIError):
    def __init__(self) -> None:
        super().__init__(409, "ALREADY_MEMBER", "User already belongs to this organization.")


class EmailAlreadyRegisteredError(APIError):
    def __init__(self) -> None:
        super().__init__(409, "EMAIL_ALREADY_REGISTERED", "That email is already registered.")


class InvalidCredentialsError(APIError):
    def __init__(self) -> None:
        super().__init__(401, "INVALID_CREDENTIALS", "Incorrect email or password.")


class InvalidTokenError(APIError):
    def __init__(self) -> None:
        super().__init__(401, "INVALID_TOKEN", "Invalid or expired token.")


class GateAlreadyDecidedError(APIError):
    def __init__(self) -> None:
        super().__init__(
            409,
            "GATE_ALREADY_DECIDED",
            "This gate already has a different recorded decision.",
        )


class DocumentsNotReadyError(APIError):
    def __init__(self) -> None:
        super().__init__(
            409,
            "DOCUMENTS_NOT_READY",
            "All uploaded documents must finish parsing before a workflow run can start.",
        )


class IdempotencyKeyRequiredError(APIError):
    def __init__(self) -> None:
        super().__init__(
            400,
            "IDEMPOTENCY_KEY_REQUIRED",
            "This operation requires an Idempotency-Key header (Part I1).",
        )


class GraphStaleAPIError(APIError):
    """G12: "Graph endpoints return 409 with code GRAPH_STALE when required
    projection version is behind" - the API-layer wrapper around domain.
    services.graph_service.GraphStaleError, same "domain exception -> APIError
    subclass -> registered handler" translation every other *NotFoundError
    already goes through in routes/proposal.py etc.
    """

    def __init__(self, current_state_version: int, projected_state_version: int | None) -> None:
        super().__init__(
            409,
            "GRAPH_STALE",
            f"Graph projection is behind (current state_version={current_state_version}, "
            f"last projected={projected_state_version}). Trigger POST .../graph/project and retry.",
        )


class LoginUnavailableError(APIError):
    # Phase 4 plan item 2 (decision 0041): POST /auth/login while the rate
    # limiter cannot reach Redis. The login limit cannot be enforced, so
    # login is refused rather than left unlimited. Returned by main.py's
    # _rate_limit middleware; the web app's login screen shows its own
    # sentence with the wait taken from Retry-After.
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            503,
            "LOGIN_UNAVAILABLE",
            "Sign-in is briefly unavailable. Please try again shortly.",
            headers={"Retry-After": str(retry_after_seconds)},
        )


class InternalServerError(APIError):
    # Returned by main.py's catch-all middleware for any exception nothing
    # else handled. The message is deliberately generic: the real exception
    # (type, message, traceback) goes to the server log only, never into a
    # response body a client can read.
    def __init__(self) -> None:
        super().__init__(
            500, "INTERNAL_ERROR", "Something went wrong on our side. Please try again."
        )


def error_response(exc: APIError, headers: dict[str, str] | None = None) -> JSONResponse:
    """The one place the {"error": {"code", "message"}} envelope is built.

    Shared by the APIError exception handler below and by main.py's
    middlewares (the 429, the 413 and the catch-all 500), which return
    before or outside FastAPI's exception handling and so cannot raise an
    APIError and let the handler build the body.

    Without `headers`, the error's own headers are sent (Retry-After on
    LOGIN_UNAVAILABLE, say). Before Phase 4 plan item 2 only `headers` was
    sent, so a middleware returning an error that carries its own headers
    lost them (caught by the login 503's route test).
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=headers if headers is not None else exc.headers,
    )


async def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return error_response(exc)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, _api_error_handler)  # type: ignore[arg-type]
