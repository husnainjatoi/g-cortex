"""Authenticated principal. Built ONLY from a verified server-side session, never from client / LLM input."""
from dataclasses import dataclass, field

from .errors import Forbidden

SCOPES = ("super", "academic", "mess", "requisition")


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str
    scopes: frozenset = field(default_factory=frozenset)
    channel: str = "web"          # 'web' | 'assistant' | 'system'
    full_name: str = ""
    must_change: bool = False     # initial credential still in use: only logout / change-password are allowed

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_student(self) -> bool:
        return self.role == "student"

    @property
    def is_faculty(self) -> bool:
        return self.role == "faculty"

    def has_scope(self, scope: str) -> bool:
        return self.is_admin and ("super" in self.scopes or scope in self.scopes)

    def require_role(self, *roles: str) -> None:
        if self.role not in roles:
            raise Forbidden(f"This action requires one of the roles: {', '.join(roles)}.")

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise Forbidden(f"This action requires the '{scope}' administrative scope.")

    def resolve_student(self, student_id: str | None, scope: str) -> str:
        """Return the student the operation applies to.

        Students may only act on themselves. Administrators holding `scope` may act on any student.
        """
        if student_id is None or student_id == self.user_id:
            return self.user_id
        if self.has_scope(scope):
            return student_id
        raise Forbidden("You may only access your own records.")

    def as_channel(self, channel: str) -> "Actor":
        return Actor(self.user_id, self.role, self.scopes, channel, self.full_name, self.must_change)


def system_actor() -> Actor:
    return Actor("SYSTEM", "admin", frozenset({"super"}), "system", "System")
