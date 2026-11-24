"""
G-Cortex Centralized RBAC Security Layer
Role-based access control utilities for the G-Cortex platform.
Defines the allowed roles, role hierarchy, and helper functions used
across all subagent REST API routers.
"""
from enum import Enum


class UserRole(str, Enum):
    """Permitted user roles in the G-Cortex RBAC model."""
    STUDENT = "student"
    FACULTY = "faculty"
    ADMIN = "admin"


# Role hierarchy (higher index = more privilege)
_ROLE_RANK: dict[UserRole, int] = {
    UserRole.STUDENT: 0,
    UserRole.FACULTY: 1,
    UserRole.ADMIN: 2,
}

# Minimum role required for each protected action
ROLE_REQUIREMENTS: dict[str, UserRole] = {
    "view_course_catalog": UserRole.STUDENT,
    "check_enrollment": UserRole.STUDENT,
    "apply_mess_rebate": UserRole.STUDENT,
    "generate_qr": UserRole.STUDENT,
    "view_menu": UserRole.STUDENT,
    "submit_society_requisition": UserRole.STUDENT,
    "reserve_sports": UserRole.STUDENT,
    "checkout_equipment": UserRole.STUDENT,
    "review_requisition": UserRole.FACULTY,
    "forecast_demand": UserRole.FACULTY,
    "enforce_auto_release": UserRole.ADMIN,
}


def has_permission(user_role: str, action: str) -> bool:
    """Check whether a given role has sufficient privilege for an action.

    Args:
        user_role: The role string from the users table (e.g. 'student').
        action: The action key from ROLE_REQUIREMENTS.

    Returns:
        True if permitted, False otherwise.
    """
    try:
        required = ROLE_REQUIREMENTS.get(action, UserRole.ADMIN)
        actual = UserRole(user_role)
        return _ROLE_RANK[actual] >= _ROLE_RANK[required]
    except (ValueError, KeyError):
        return False


def validate_role(role: str) -> bool:
    """Return True if the string matches a valid G-Cortex user role."""
    return role in {r.value for r in UserRole}
