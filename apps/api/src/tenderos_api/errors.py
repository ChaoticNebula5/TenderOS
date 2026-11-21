from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from tenderos_api.client_disconnect import client_closed_response


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        details: dict[str, str | None] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        # Response headers for this error, e.g. Retry-After. Sent by the
        # exception handler below; None for almost every error.
        self.headers = headers
        # Machine-readable facts a client acts on (the active run's id on
        # WORKFLOW_RUN_ACTIVE), sent as error.details only when set, so every
        # other error's body is unchanged. A value may be None where the fact
        # is "nobody" (GATE_ALREADY_DECIDED on a gate a cancel closed).
        self.details = details


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
    # B12: routes/gates.py's own enforcement of the Build Guide's stop-gate
    # rule (paraphrased): approval is refused until every blocker is resolved
    # or an authorized actor explicitly waives it - decide_gate raises this instead of ever
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


class ReadinessChangedError(APIError):
    # Plan item 93 (W2): the final gate's approval recomputes readiness and
    # refuses when the result is not the report that was current before it
    # (the one the Readiness page shows), or when the bid's state moved
    # before the decision could be recorded. The recomputed report is now
    # the current one, so reviewing it and approving again goes through.
    def __init__(self) -> None:
        super().__init__(
            409,
            "READINESS_CHANGED",
            "Readiness changed since the report you saw. Review the current readiness "
            "report, then decide again.",
        )


class GateTargetUpdatesError(APIError):
    # Plan item 21's note with item 67's gate-target note (PR 16, user
    # decision 2026-11-17): a gate decision decides the gate only. Each item
    # is approved, selected or resolved on its own route. A decision that
    # still carries target_updates is refused before anything is read or
    # written, on every gate. The field stays on the request so an empty
    # list (the spec's example payload) is still accepted. Until PR 16 these
    # updates were applied in their own committed transaction before the
    # decision, so a refused decision left them applied, and they were
    # checked only against the gate's tender, so one gate's decision could
    # change another gate's targets (both observed). No screen sent them.
    def __init__(self) -> None:
        super().__init__(
            422,
            "GATE_TARGET_UPDATES",
            "Gate decisions take no target_updates; approve, select or resolve each item "
            "on its own screen, then decide the gate.",
        )


class ArchivedEmployeeSelectionAPIError(APIError):
    # Plan item 86, option A: routes/talent.py's
    # translation of talent_service.ArchivedEmployeeSelectionError. 409, as
    # verify answers for the same employee: the request is well formed, the
    # employee's state refuses it, and restoring the employee clears it.
    def __init__(self) -> None:
        super().__init__(
            409,
            "EMPLOYEE_ARCHIVED",
            "Employee is archived (removed from the roster); restore it before selecting.",
        )


class ComplianceEntryChangedAPIError(APIError):
    # Plan item 59 (Q4): an approval named a status the entry no longer has,
    # so the screen was loaded before the entry changed. Nothing was written;
    # details.current_status lets the screen say what it is now.
    def __init__(self, current_status: str) -> None:
        super().__init__(
            409,
            "COMPLIANCE_ENTRY_CHANGED",
            "This entry's status changed since the page loaded; review it and approve again.",
            details={"current_status": current_status},
        )


class ImportItemDecisionError(APIError):
    # Plan item 80 (user decisions 2026-11-18): routes/knowledge.py's
    # translations of knowledge_service's approve and reject refusals. 409
    # where the item's or the employee's state refuses a well-formed
    # request; 422 for an approval that leaves out the fingerprint.
    @classmethod
    def not_in_review(cls, item_status: str) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_NOT_IN_REVIEW",
            f"This import item is {item_status}; only an item in review can be decided.",
        )

    @classmethod
    def no_target(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_NO_TARGET",
            "This item names no employee to apply it to (for a certification: no holder "
            "was chosen); choose one, or reject it.",
        )

    @classmethod
    def source_missing(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_SOURCE_MISSING",
            "This import item's source document is gone (the resume, or the certification "
            "sheet); upload it again.",
        )

    @classmethod
    def fingerprint_required(cls) -> "ImportItemDecisionError":
        return cls(
            422,
            "IMPORT_ITEM_FINGERPRINT_REQUIRED",
            "Approving this item needs the fingerprint of the values shown "
            "(target_fingerprint, and candidates_fingerprint for a choice among candidates).",
        )

    @classmethod
    def payload_invalid(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_PAYLOAD_INVALID",
            "This item's extracted fields could not be read, so what approving would change "
            "cannot be shown; reject it, or upload the file again.",
        )

    @classmethod
    def target_changed(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_TARGET_CHANGED",
            "The employee changed after this item was loaded. Review the updated values, "
            "then approve again.",
        )

    @classmethod
    def target_archived(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "EMPLOYEE_ARCHIVED",
            "The chosen employee is archived (removed from the roster); restore them before "
            "approving.",
        )

    @classmethod
    def candidates_changed(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_CANDIDATES_CHANGED",
            "The employees matching this item changed after it was loaded. Review the "
            "updated list, then decide again.",
        )

    @classmethod
    def not_a_candidate(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_NOT_A_CANDIDATE",
            "That employee is not one this item can go to; choose one of the employees shown.",
        )

    @classmethod
    def strong_match(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_STRONG_MATCH",
            "An employee on file already has this item's email, document or employee code, "
            "so it cannot become a new person; apply it to that employee instead.",
        )

    @classmethod
    def type_unsupported(cls) -> "ImportItemDecisionError":
        return cls(
            409,
            "IMPORT_ITEM_TYPE_UNSUPPORTED",
            "Choosing an employee is for resume, employee-master and certification items, and "
            "importing as a new person for resume and employee-master items.",
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


class CsrfCheckFailedError(APIError):
    # Plan item 34, option 2: login, register, refresh and logout need the
    # web client's header, and a browser Origin of WEB_BASE_URL
    # (session_cookie.require_web_client).
    def __init__(self) -> None:
        super().__init__(
            403, "CSRF_CHECK_FAILED", "This request must come from the TenderOS web app."
        )


class GateAlreadyDecidedError(APIError):
    # Q7 (PR 20): also the answer to another approver's same decision, so
    # the message no longer says "different". details say who decided
    # (decided_by, and decided_by_name: their display name, else their
    # email), when (decided_at, ISO 8601) and what (decision); the first
    # two and the last are None for a gate a cancel closed. The gate panel
    # builds its sentence from them.
    def __init__(
        self,
        decided_by: str | None = None,
        decided_by_name: str | None = None,
        decided_at: str | None = None,
        decision: str | None = None,
    ) -> None:
        super().__init__(
            409,
            "GATE_ALREADY_DECIDED",
            "This gate has already been decided.",
            details={
                "decided_by": decided_by,
                "decided_by_name": decided_by_name,
                "decided_at": decided_at,
                "decision": decision,
            },
        )


class DocumentsNotReadyError(APIError):
    def __init__(self) -> None:
        super().__init__(
            409,
            "DOCUMENTS_NOT_READY",
            "All uploaded documents must finish parsing before a workflow run can start.",
        )


class WorkflowRunActiveAPIError(APIError):
    """Phase 4 plan item 43: the tender already has an active run. The id is
    in error.details.active_run_id, so the screen can show that run as if
    it had started it."""

    def __init__(self, active_run_id: str) -> None:
        super().__init__(
            409,
            "WORKFLOW_RUN_ACTIVE",
            "This tender already has a workflow run in progress.",
            details={"active_run_id": active_run_id},
        )


class IdempotencyKeyRequiredError(APIError):
    def __init__(self) -> None:
        super().__init__(
            400,
            "IDEMPOTENCY_KEY_REQUIRED",
            "This operation requires an Idempotency-Key header (Part I1).",
        )


class GraphStaleAPIError(APIError):
    """G12: a graph endpoint answers 409 GRAPH_STALE when the projection is
    older than the version the request needs - the API-layer wrapper around domain.
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
    error: dict[str, object] = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        error["details"] = exc.details
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
        headers=headers if headers is not None else exc.headers,
    )


async def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return error_response(exc)


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """FastAPI's own handler, except for the 400 FastAPI raises when the
    client disconnected while it read a route's body parameter
    (client_disconnect.py)."""
    if isinstance(exc.__cause__, ClientDisconnect):
        return client_closed_response(request)
    return await http_exception_handler(request, exc)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, _api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
