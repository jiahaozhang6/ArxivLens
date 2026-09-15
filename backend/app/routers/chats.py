from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_admin
from app.config import get_settings
from app.database import SessionLocal, get_session
from app.models import (
    AdminUser,
    AnalysisStatus,
    ChatMessageStatus,
    LLMProfile,
    Paper,
    PaperChatMessage,
    PaperChatSession,
    utcnow,
)
from app.schemas import (
    PaperChatMessageCreate,
    PaperChatSessionCreate,
    PaperChatSessionDetail,
    PaperChatSessionOut,
)
from app.services.llm import stream_chat_with_profile
from app.services.model_routing import stream_with_profile_fallback
from app.services.paper_chat import build_paper_chat_prompt, select_chat_history
from app.services.settings_service import get_default_profile_id

router = APIRouter(
    prefix="/api/papers/{paper_id}/chat",
    tags=["paper-chat"],
    dependencies=[Depends(require_admin)],
)
_chat_generation_semaphore = asyncio.Semaphore(max(get_settings().chat_concurrency, 1))
_chat_rate_lock = asyncio.Lock()
_chat_request_times: dict[int, deque[float]] = {}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _enforce_chat_rate_limit(user_id: int) -> None:
    limit = max(get_settings().chat_requests_per_minute, 1)
    now = time.monotonic()
    async with _chat_rate_lock:
        request_times = _chat_request_times.setdefault(user_id, deque())
        while request_times and now - request_times[0] >= 60:
            request_times.popleft()
        if len(request_times) >= limit:
            retry_after = max(int(60 - (now - request_times[0])), 1)
            raise HTTPException(
                status_code=429,
                detail=f"论文问答调用过于频繁，请在 {retry_after} 秒后重试",
                headers={"Retry-After": str(retry_after)},
            )
        request_times.append(now)


async def _get_chat_session(
    db: AsyncSession,
    paper_id: int,
    chat_session_id: int,
    *,
    with_messages: bool = False,
) -> PaperChatSession:
    statement = select(PaperChatSession).where(
        PaperChatSession.id == chat_session_id,
        PaperChatSession.paper_id == paper_id,
    )
    if with_messages:
        statement = statement.options(selectinload(PaperChatSession.messages))
    chat_session = await db.scalar(statement)
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Paper chat session not found")
    return chat_session


@router.get("/sessions", response_model=list[PaperChatSessionOut])
async def list_chat_sessions(
    paper_id: int,
    session: AsyncSession = Depends(get_session),
):
    if await session.get(Paper, paper_id) is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return list(
        await session.scalars(
            select(PaperChatSession)
            .where(PaperChatSession.paper_id == paper_id)
            .order_by(PaperChatSession.updated_at.desc(), PaperChatSession.id.desc())
        )
    )


@router.post(
    "/sessions",
    response_model=PaperChatSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_session(
    paper_id: int,
    payload: PaperChatSessionCreate,
    session: AsyncSession = Depends(get_session),
):
    if await session.get(Paper, paper_id) is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if payload.llm_profile_id is not None:
        profile = await session.get(LLMProfile, payload.llm_profile_id)
        if profile is None or not profile.enabled:
            raise HTTPException(status_code=400, detail="Select an enabled cloud model profile")
    profile_id = payload.llm_profile_id or await get_default_profile_id(session)
    chat_session = PaperChatSession(
        paper_id=paper_id,
        title=(payload.title or "新对话").strip()[:200] or "新对话",
        preferred_llm_profile_id=profile_id,
    )
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)
    return chat_session


@router.get("/sessions/{chat_session_id}", response_model=PaperChatSessionDetail)
async def get_chat_session(
    paper_id: int,
    chat_session_id: int,
    session: AsyncSession = Depends(get_session),
):
    chat_session = await _get_chat_session(
        session, paper_id, chat_session_id, with_messages=True
    )
    chat_session.messages.sort(key=lambda item: item.id)
    return chat_session


@router.delete("/sessions/{chat_session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    paper_id: int,
    chat_session_id: int,
    session: AsyncSession = Depends(get_session),
):
    chat_session = await _get_chat_session(session, paper_id, chat_session_id)
    await session.delete(chat_session)
    await session.commit()


async def _mark_cancelled(message_id: int, content: str) -> None:
    async with SessionLocal() as session:
        message = await session.get(PaperChatMessage, message_id)
        if message is None or message.status != ChatMessageStatus.streaming:
            return
        message.content = content
        message.status = ChatMessageStatus.cancelled
        message.error_message = "用户停止了生成"
        message.completed_at = utcnow()
        await session.commit()


async def _stream_answer(
    *,
    assistant_message_id: int,
    chat_session_id: int,
    preferred_profile_id: int | None,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> AsyncIterator[str]:
    content = ""
    yield _sse("ready", {"assistant_message_id": assistant_message_id})
    try:
        async with SessionLocal() as session:
            assistant = await session.get(PaperChatMessage, assistant_message_id)
            chat_session = await session.get(PaperChatSession, chat_session_id)
            if assistant is None or chat_session is None:
                yield _sse("error", {"message": "Chat record disappeared before generation"})
                return

            settings = get_settings()
            async with _chat_generation_semaphore:
                stream = stream_with_profile_fallback(
                    session,
                    preferred_profile_id,
                    lambda profile: stream_chat_with_profile(
                        profile,
                        system_prompt,
                        messages,
                        settings.chat_max_output_tokens,
                    ),
                )
                async for event in stream:
                    event_type = event["type"]
                    if event_type == "model":
                        profile = event["profile"]
                        assistant.llm_profile_id = profile["profile_id"]
                        assistant.provider = profile["provider"]
                        assistant.model = profile["model"]
                        await session.commit()
                        yield _sse("model", event)
                    elif event_type == "reset":
                        content = ""
                        assistant.content = ""
                        await session.commit()
                        yield _sse("reset", event)
                    elif event_type == "delta":
                        content += event["text"]
                        yield _sse("delta", {"text": event["text"]})
                    elif event_type == "done":
                        assistant.content = content
                        assistant.status = ChatMessageStatus.completed
                        assistant.model_routing = event["routing"]
                        assistant.error_message = event.get("warning")
                        assistant.completed_at = utcnow()
                        chat_session.updated_at = utcnow()
                        await session.commit()
                        yield _sse(
                            "done",
                            {
                                "message_id": assistant.id,
                                "routing": event["routing"],
                                "warning": event.get("warning"),
                            },
                        )
    except asyncio.CancelledError:
        await asyncio.shield(_mark_cancelled(assistant_message_id, content))
        raise
    except Exception as exc:
        error = str(exc)[:4000]
        async with SessionLocal() as session:
            assistant = await session.get(PaperChatMessage, assistant_message_id)
            if assistant is not None:
                assistant.content = content
                assistant.status = ChatMessageStatus.failed
                assistant.error_message = error
                assistant.completed_at = utcnow()
                await session.commit()
        yield _sse("error", {"message": error})


@router.post("/sessions/{chat_session_id}/messages/stream")
async def stream_chat_message(
    paper_id: int,
    chat_session_id: int,
    payload: PaperChatMessageCreate,
    admin: AdminUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await _enforce_chat_rate_limit(admin.id)
    paper = await session.scalar(
        select(Paper)
        .where(Paper.id == paper_id)
        .options(selectinload(Paper.analyses))
    )
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    chat_session = await _get_chat_session(session, paper_id, chat_session_id)
    active_message = await session.scalar(
        select(PaperChatMessage.id).where(
            PaperChatMessage.session_id == chat_session.id,
            PaperChatMessage.status == ChatMessageStatus.streaming,
        )
    )
    if active_message is not None:
        raise HTTPException(status_code=409, detail="This conversation is already generating")

    if payload.llm_profile_id is not None:
        profile = await session.get(LLMProfile, payload.llm_profile_id)
        if profile is None or not profile.enabled:
            raise HTTPException(status_code=400, detail="Select an enabled cloud model profile")
    preferred_profile_id = (
        payload.llm_profile_id
        or chat_session.preferred_llm_profile_id
        or await get_default_profile_id(session)
    )
    if preferred_profile_id is None:
        raise HTTPException(status_code=400, detail="Configure an enabled cloud model first")

    history_records = list(
        await session.scalars(
            select(PaperChatMessage)
            .where(PaperChatMessage.session_id == chat_session.id)
            .order_by(PaperChatMessage.id)
        )
    )
    settings = get_settings()
    history = select_chat_history(
        history_records,
        max_messages=settings.chat_history_messages,
        max_chars=settings.chat_history_max_chars,
    )
    user_message = PaperChatMessage(
        session_id=chat_session.id,
        role="user",
        content=payload.content,
        status=ChatMessageStatus.completed,
        completed_at=utcnow(),
    )
    assistant_message = PaperChatMessage(
        session_id=chat_session.id,
        role="assistant",
        content="",
        status=ChatMessageStatus.streaming,
        llm_profile_id=preferred_profile_id,
    )
    session.add_all([user_message, assistant_message])
    if not history_records and chat_session.title == "新对话":
        chat_session.title = payload.content.replace("\n", " ")[:36]
    chat_session.preferred_llm_profile_id = preferred_profile_id
    chat_session.updated_at = utcnow()
    await session.commit()
    await session.refresh(assistant_message)

    latest_analysis = max(
        (item for item in paper.analyses if item.status == AnalysisStatus.completed),
        key=lambda item: item.id,
        default=None,
    )
    model_messages = [*history, {"role": "user", "content": payload.content}]
    return StreamingResponse(
        _stream_answer(
            assistant_message_id=assistant_message.id,
            chat_session_id=chat_session.id,
            preferred_profile_id=preferred_profile_id,
            system_prompt=build_paper_chat_prompt(paper, latest_analysis),
            messages=model_messages,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
