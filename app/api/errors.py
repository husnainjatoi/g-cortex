"""Plain-language API errors. Technical detail (validation internals, stack traces, DB errors) is logged server-side only."""
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("g_cortex.errors")

GENERIC_VALIDATION = "Some of the information you entered is missing or invalid. Please check the form and try again."
GENERIC_SERVER = "Something went wrong while processing your request. Please try again later."

LABELS = {
    "description": "description", "attendees": "number of participants", "expected_attendees": "number of participants", "facilities": "list of facilities", "required_facilities": "list of facilities",
    "event_title": "event title", "booking_date": "date", "date": "date", "start_time": "start time", "duration_hours": "duration", "venue_id": "venue", "venue_type": "venue type",
    "society_name": "society", "society_id": "society", "reservation_type": "reservation type", "justification": "purpose", "purpose": "purpose", "email": "email address",
    "password": "password", "new_password": "new password", "confirm_password": "password confirmation", "current_password": "current password", "reason": "reason", "reference": "payment reference",
    "amount_pkr": "amount", "barcode": "equipment barcode", "message": "message", "question": "question", "start_date": "start date", "end_date": "end date", "title": "title", "credits": "credit hours",
    "course_code": "course code", "capacity": "capacity", "user_id": "user ID", "full_name": "full name", "note": "note", "comments": "comments", "days": "number of days", "student_id": "student ID",
    "date_of_birth": "date of birth", "meal_type": "meal", "rate_pkr": "rate", "approve": "decision", "action": "action", "pin": "check-in PIN", "facility_id": "facility", "duration": "duration",
}


def label_for(loc) -> str:
    name = next((str(p) for p in reversed(loc) if isinstance(p, str) and p not in ("body", "query", "path")), "this field")
    return LABELS.get(name, name.replace("_", " "))


def field_name(loc) -> str:
    return next((str(p) for p in reversed(loc) if isinstance(p, str) and p not in ("body", "query", "path")), "")


def friendly_message(err: dict) -> str:
    t, ctx, label = err.get("type", ""), err.get("ctx") or {}, label_for(err.get("loc", ()))
    if t == "missing":
        return f"Please fill in the {label}."
    if t in ("string_too_short", "too_short"):
        n = ctx.get("min_length", 1)
        return f"Please fill in the {label}." if n <= 1 else f"Please enter a {label} containing at least {n} characters."
    if t in ("string_too_long", "too_long"):
        return f"The {label} is too long (at most {ctx.get('max_length')} characters)."
    if t in ("greater_than_equal",):
        return f"The {label} must be at least {ctx.get('ge')}."
    if t == "greater_than":
        return f"The {label} must be greater than {ctx.get('gt')}."
    if t == "less_than_equal":
        return f"The {label} must be {ctx.get('le')} or less."
    if t == "less_than":
        return f"The {label} must be less than {ctx.get('lt')}."
    if t in ("int_parsing", "float_parsing", "int_type", "float_type", "decimal_parsing", "int_from_float"):
        return f"The {label} must be a valid number."
    if t in ("bool_parsing", "bool_type"):
        return f"Please choose a valid option for the {label}."
    if t in ("string_type", "literal_error", "enum"):
        return f"Please enter a valid {label}."
    if t in ("json_invalid", "model_attributes_type", "dict_type"):
        return GENERIC_VALIDATION
    if t == "string_pattern_mismatch":
        return f"The {label} is not in the expected format."
    return f"Please check the {label}."


async def validation_handler(request: Request, exc: RequestValidationError):
    logger.info("validation failed on %s: %s", request.url.path, exc.errors())
    fields: dict[str, str] = {}
    messages: list[str] = []
    for e in exc.errors():
        m = friendly_message(e)
        messages.append(m)
        fields.setdefault(field_name(e.get("loc", ())), m)
    detail = messages[0] if len(messages) == 1 else GENERIC_VALIDATION
    return JSONResponse(status_code=422, content={"detail": detail, "code": "validation_failed", "fields": {k: v for k, v in fields.items() if k}, "messages": messages})


_STOCK = {"Not Found", "Method Not Allowed", "Forbidden", "Unauthorized", "Bad Request", "Internal Server Error", "Unprocessable Entity", "Not authenticated"}
_BY_STATUS = {400: "We couldn't process that request. Please check your information and try again.", 401: "Please sign in to continue.", 403: "You don't have permission to do that.",
              404: "We couldn't find what you were looking for.", 405: "That action isn't supported.", 409: "That conflicts with existing information. Please review and try again.",
              413: "The information you sent is too large.", 429: "You're doing that too quickly. Please wait a moment and try again."}


async def http_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail not in _STOCK and not exc.detail.startswith("{") else None
    if exc.status_code >= 500:
        logger.error("HTTP %s on %s: %s", exc.status_code, request.url.path, exc.detail)
        detail = GENERIC_SERVER
    return JSONResponse(status_code=exc.status_code, content={"detail": detail or _BY_STATUS.get(exc.status_code, GENERIC_VALIDATION if exc.status_code < 500 else GENERIC_SERVER), "code": "http_error"})


async def unhandled_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)      # full detail stays in the server log
    return JSONResponse(status_code=500, content={"detail": GENERIC_SERVER, "code": "server_error"})
