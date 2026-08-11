from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class IdempotencyConflictError(APIError):
    def __init__(self) -> None:
        super().__init__(
            409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key reused with a different request body."
        )


class OrgAccessDeniedError(APIError):
    def __init__(self) -> None:
        super().__init__(403, "ORG_ACCESS_DENIED", "Not a member of this organization.")


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


async def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, _api_error_handler)  # type: ignore[arg-type]
