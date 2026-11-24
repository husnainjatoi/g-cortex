"""
G-Cortex Central Orchestrator REST Router
Provides endpoints for chat messaging, session management,
conversation history retrieval, and subagent status inspections.
"""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.agents.central_orchestrator import central_orchestrator, session_manager
from app.agents.central_orchestrator.agent import SUBAGENT_LABELS

router = APIRouter()


# ── Request / Response Models ───────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        description="Natural language user query for the G-Cortex multi-agent system",
        examples=["What are the prerequisites for CS325?"],
    )
    session_id: Optional[str] = Field(
        None,
        description="Active session ID. If omitted or not found, a new session will be created.",
    )
    user_id: Optional[str] = Field(
        "student_default",
        description="Student registration or user identifier (e.g. '2021001')",
    )


class ChatResponse(BaseModel):
    session_id: str
    user_id: str
    query: str
    reply: str
    routed_to: List[str]
    routed_to_labels: List[str]
    intent: str
    reasoning: Optional[str] = None


class NewSessionRequest(BaseModel):
    user_id: Optional[str] = Field(
        "student_default", description="User ID for the new session"
    )


class SessionCreatedResponse(BaseModel):
    session_id: str
    user_id: str
    created_at: str
    status: str = "active"


class MessageRecord(BaseModel):
    id: str
    role: str
    content: str
    timestamp: str
    routed_to: List[str]
    intent: Optional[str] = None
    metadata: Dict[str, Any] = {}


class SessionHistoryResponse(BaseModel):
    session_id: str
    user_id: str
    created_at: str
    last_active: str
    message_count: int
    messages: List[MessageRecord]


# ── Endpoints ───────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse, summary="Send message to Central Orchestrator")
async def send_chat_message(payload: ChatRequest):
    """Primary chat endpoint.

    Accepts natural-language queries, understands intent via Gemini/ADK,
    routes to the appropriate subagent(s), and returns a structured response.
    """
    clean_msg = payload.message.strip()
    if not clean_msg:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be blank or whitespace only.",
        )

    try:
        result = await central_orchestrator.process_query(
            query=clean_msg,
            session_id=payload.session_id,
            user_id=payload.user_id or "student_default",
        )
        return ChatResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Orchestration error processing query: {str(e)}",
        )


@router.post(
    "/session/new",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new conversation session",
)
def create_new_session(payload: Optional[NewSessionRequest] = None):
    """Initializes a new, isolated conversation session with its own memory context."""
    user_id = payload.user_id if payload and payload.user_id else "student_default"
    session = session_manager.create_session(user_id=user_id)
    return SessionCreatedResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        created_at=session.created_at,
    )


@router.get(
    "/session/{session_id}/history",
    response_model=SessionHistoryResponse,
    summary="Get conversation history for a session",
)
def get_session_history(session_id: str):
    """Retrieves all historical message turns within an active session."""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    return SessionHistoryResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        created_at=session.created_at,
        last_active=session.last_active,
        message_count=len(session.messages),
        messages=[MessageRecord(**m.to_dict()) for m in session.messages],
    )


@router.delete(
    "/session/{session_id}",
    summary="Clear or delete a conversation session",
)
def delete_session(session_id: str):
    """Deletes a conversation session and purges its stored memory context."""
    deleted = session_manager.delete_session(session_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return {
        "status": "success",
        "message": f"Session '{session_id}' deleted successfully.",
    }


@router.get("/sessions", summary="List all active conversation sessions")
def list_sessions():
    """Lists all active in-memory conversation sessions."""
    return {"sessions": session_manager.list_sessions()}


@router.get("/status", summary="Get Central Orchestrator & Subagent Status")
def orchestrator_status():
    """Returns the operational status of the Central Orchestrator and connected subagents."""
    return {
        "orchestrator": {
            "status": "online",
            "model": central_orchestrator.model_name,
            "has_llm_client": central_orchestrator.client is not None,
            "active_sessions": len(session_manager.list_sessions()),
        },
        "subagents": {
            "course_management_subagent": {
                "label": SUBAGENT_LABELS["course_management_subagent"],
                "status": "online",
                "tools_count": 8,
            },
            "mess_management_subagent": {
                "label": SUBAGENT_LABELS["mess_management_subagent"],
                "status": "online",
                "tools_count": 6,
            },
            "requisition_management_subagent": {
                "label": SUBAGENT_LABELS["requisition_management_subagent"],
                "status": "online",
                "tools_count": 12,
            },
        },
    }
