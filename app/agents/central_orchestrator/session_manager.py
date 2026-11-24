"""
G-Cortex Central Orchestrator: Session & Conversation Manager
Manages multi-turn conversation sessions, context histories,
and provides context-awareness for follow-up query disambiguation.
"""
import uuid
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class Message:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    role: str = "user"  # "user" | "assistant" | "system"
    content: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    routed_to: List[str] = field(default_factory=list)
    intent: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = "student_default"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_active: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    messages: List[Message] = field(default_factory=list)

    def add_message(
        self,
        role: str,
        content: str,
        routed_to: Optional[List[str]] = None,
        intent: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        msg = Message(
            role=role,
            content=content,
            routed_to=routed_to or [],
            intent=intent,
            metadata=metadata or {},
        )
        self.messages.append(msg)
        self.last_active = datetime.now(timezone.utc).isoformat()
        return msg

    def get_recent_history(self, max_turns: int = 6) -> List[Message]:
        """Returns the most recent N messages for context augmentation."""
        return self.messages[-max_turns:] if self.messages else []

    def format_history_for_llm(self, max_turns: int = 6) -> str:
        """Formats recent history as a readable dialogue transcript."""
        recent = self.get_recent_history(max_turns)
        if not recent:
            return ""
        lines = []
        for m in recent:
            role_label = "User" if m.role == "user" else "Assistant"
            lines.append(f"{role_label}: {m.content}")
        return "\n".join(lines)

    def clear(self) -> None:
        """Clears all conversation messages."""
        self.messages.clear()
        self.last_active = datetime.now(timezone.utc).isoformat()


class SessionManager:
    """Thread-safe session repository for G-Cortex Central Orchestrator."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SessionManager, cls).__new__(cls)
                cls._instance._sessions: Dict[str, Session] = {}
            return cls._instance

    def create_session(self, user_id: str = "student_default") -> Session:
        with self._lock:
            session = Session(user_id=user_id)
            self._sessions[session.session_id] = session
            return session

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create_session(
        self, session_id: Optional[str] = None, user_id: str = "student_default"
    ) -> Session:
        with self._lock:
            if session_id and session_id in self._sessions:
                return self._sessions[session_id]
            new_session = Session(
                session_id=session_id or str(uuid.uuid4()), user_id=user_id
            )
            self._sessions[new_session.session_id] = new_session
            return new_session

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "session_id": s.session_id,
                    "user_id": s.user_id,
                    "created_at": s.created_at,
                    "last_active": s.last_active,
                    "message_count": len(s.messages),
                }
                for s in self._sessions.values()
            ]

    def clear_all(self) -> None:
        with self._lock:
            self._sessions.clear()


# Global singleton instance
session_manager = SessionManager()
