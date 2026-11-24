"""Typed service errors. The API layer maps these to HTTP codes; the assistant reports them verbatim."""


class ServiceError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"ok": False, "code": self.code, "error": self.message, "details": self.details}


class ValidationFailed(ServiceError):
    status, code = 422, "validation_failed"


class RuleViolation(ServiceError):
    """A business / institutional rule forbids the operation (prerequisite, quota, notice period...)."""
    status, code = 409, "rule_violation"


class Forbidden(ServiceError):
    status, code = 403, "forbidden"


class Unauthenticated(ServiceError):
    status, code = 401, "unauthenticated"


class NotFound(ServiceError):
    status, code = 404, "not_found"


class Conflict(ServiceError):
    status, code = 409, "conflict"
