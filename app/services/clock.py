"""Single source of 'now' so rules (notice periods, check-in windows, TOTP) are testable."""
import datetime

_offset: datetime.timedelta | None = None
_fixed: datetime.datetime | None = None


def now() -> datetime.datetime:
    return _fixed if _fixed else datetime.datetime.now()


def today() -> datetime.date:
    return now().date()


def freeze(dt: datetime.datetime | None) -> None:
    """Test helper: pin the clock (pass None to release)."""
    global _fixed
    _fixed = dt


def parse_date(value, field: str = "date") -> datetime.date:
    from .errors import ValidationFailed
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        raise ValidationFailed(f"{field} must be a valid date in YYYY-MM-DD format.")


def parse_time(value, field: str = "time") -> datetime.time:
    from .errors import ValidationFailed
    try:
        h, m = str(value).strip().split(":")[:2]
        return datetime.time(int(h), int(m))
    except Exception:
        raise ValidationFailed(f"{field} must be a valid time in HH:MM (24-hour) format.")
