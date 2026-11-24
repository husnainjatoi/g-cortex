"""Assistant REST API. Every call is authenticated; the assistant acts only as the signed-in user."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.agents.central_orchestrator import central_orchestrator, session_manager
from app.agents.central_orchestrator.agent import SUBAGENT_LABELS
from app.api.deps import current_actor
from app.services import pending_actions
from app.services.actor import Actor

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="Natural-language request")
    session_id: Optional[str] = Field(None, max_length=64, description="Existing conversation id; omitted/unknown starts a new one")


class ChatResponse(BaseModel):
    session_id: str
    user_id: str
    query: str
    reply: str
    routed_to: List[str]
    routed_to_labels: List[str]
    intent: str
    reasoning: Optional[str] = None
    pending_actions: List[Dict[str, Any]] = []
    executed: Optional[Dict[str, Any]] = None


@router.post("/chat", response_model=ChatResponse, summary="Send a message to the G-Cortex assistant")
async def send_chat_message(payload: ChatRequest, actor: Actor = Depends(current_actor)):
    clean = payload.message.strip()
    if not clean:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message cannot be blank.")
    result = await central_orchestrator.process_query(clean, payload.session_id, actor)
    return ChatResponse(**result)


@router.post("/actions/{action_id}/confirm", summary="Confirm a staged action (button equivalent of replying 'yes')")
def confirm_action(action_id: str, actor: Actor = Depends(current_actor)):
    out = pending_actions.confirm(actor, action_id)
    return out


@router.post("/actions/{action_id}/cancel", summary="Cancel a staged action")
def cancel_action(action_id: str, actor: Actor = Depends(current_actor)):
    return pending_actions.cancel(actor, action_id)


@router.post("/session", summary="Start a fresh conversation")
def create_new_session(actor: Actor = Depends(current_actor)):
    s = session_manager.create_session(user_id=actor.user_id)
    return {"session_id": s.session_id, "user_id": s.user_id, "created_at": s.created_at, "status": "active"}


@router.get("/session/{session_id}/history", summary="Conversation history (own sessions only)")
def get_session_history(session_id: str, actor: Actor = Depends(current_actor)):
    s = session_manager.get_session(session_id, actor.user_id)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
    pending = pending_actions.pending_for_session(actor, session_id)
    return {"session_id": s.session_id, "user_id": s.user_id, "created_at": s.created_at, "last_active": s.last_active,
            "message_count": len(s.messages), "messages": [m.to_dict() for m in s.messages],
            "pending_actions": [{"action_id": p["action_id"], "summary": p["summary"], "tool": p["tool"]} for p in pending]}


@router.delete("/session/{session_id}", summary="Delete a conversation")
def delete_session(session_id: str, actor: Actor = Depends(current_actor)):
    if not session_manager.delete_session(session_id, actor.user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
    return {"deleted": True}


@router.get("/sessions", summary="Your conversations")
def list_sessions(actor: Actor = Depends(current_actor)):
    return session_manager.list_sessions(actor.user_id)


@router.get("/status", summary="Assistant status")
def orchestrator_status(actor: Actor = Depends(current_actor)):
    return {"orchestrator": "online" if central_orchestrator.client else "model_unavailable",
            "model": central_orchestrator.model_name, "subagents": [{"id": k, "label": v} for k, v in SUBAGENT_LABELS.items() if k != "central_orchestrator"]}
