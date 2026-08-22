import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..assistant_service import answer_assistant
from ..database import get_db
from ..models import AssistantConversation, AssistantMessage, User
from ..schemas import AssistantIn, AssistantOut
from ..security import current_user, require_inspection

router = APIRouter(prefix="/assistant", tags=["assistant"])


def _answer(payload: AssistantIn, db: Session, user: User) -> AssistantOut:
    inspection = None
    if payload.context.inspection_id:
        inspection = require_inspection(db, user, payload.context.inspection_id)
    conversation = None
    if payload.conversation_id:
        conversation = db.get(AssistantConversation, payload.conversation_id)
        if not conversation or conversation.user_id != user.id:
            raise HTTPException(404, "对话不存在")
        if conversation.inspection_id and (
            not inspection or conversation.inspection_id != inspection.id
        ):
            raise HTTPException(400, "对话不属于当前检查")
    if not conversation:
        conversation = AssistantConversation(
            inspection_id=inspection.id if inspection else None,
            user_id=user.id,
            title=payload.message[:80],
        )
        db.add(conversation)
        db.flush()
    db.add(
        AssistantMessage(
            conversation_id=conversation.id,
            role="user",
            content=payload.message,
            page_context=payload.context.model_dump(mode="json"),
        )
    )
    content, citations, actions = answer_assistant(
        db, payload.message, inspection, payload.context.model_dump(mode="json")
    )
    message = AssistantMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=content,
        citations=citations,
        actions=actions,
        page_context=payload.context.model_dump(mode="json"),
    )
    db.add(message)
    db.commit()
    return AssistantOut(
        conversation_id=conversation.id,
        message_id=message.id,
        content=content,
        citations=citations,
        actions=actions,
    )


@router.post("/messages", response_model=AssistantOut)
def ask_assistant(
    payload: AssistantIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> AssistantOut:
    return _answer(payload, db, user)


@router.post("/stream")
def stream_assistant(
    payload: AssistantIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> StreamingResponse:
    result = _answer(payload, db, user)

    def events():
        start = json.dumps(
            {
                "conversation_id": str(result.conversation_id),
                "message_id": str(result.message_id),
            },
            ensure_ascii=False,
        )
        yield f"event: start\ndata: {start}\n\n"
        for index in range(0, len(result.content), 24):
            token = json.dumps({"text": result.content[index : index + 24]}, ensure_ascii=False)
            yield f"event: token\ndata: {token}\n\n"
        done = json.dumps(
            {
                "citations": result.citations,
                "actions": [action.model_dump(mode="json") for action in result.actions],
            },
            ensure_ascii=False,
        )
        yield f"event: done\ndata: {done}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
