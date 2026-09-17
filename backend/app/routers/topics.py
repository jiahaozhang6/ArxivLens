from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin, require_reader
from app.database import get_session
from app.models import LLMProfile, Topic
from app.schemas import (
    TopicCreate,
    TopicOut,
    TopicPreviewRequest,
    TopicQuerySuggestionOut,
    TopicQuerySuggestionRequest,
    TopicUpdate,
)
from app.services.arxiv import fetch_papers
from app.services.llm import suggest_topic_query
from app.services.model_routing import run_with_profile_fallback

router = APIRouter(
    prefix="/api/topics",
    tags=["topics"],
    dependencies=[Depends(require_reader)],
)


async def _validate_profile(session: AsyncSession, profile_id: int | None) -> None:
    if profile_id is not None and await session.get(LLMProfile, profile_id) is None:
        raise HTTPException(status_code=400, detail="LLM profile not found")


async def _resolve_query_profile_id(
    session: AsyncSession,
    profile_id: int | None,
) -> int:
    if profile_id is not None:
        profile = await session.get(LLMProfile, profile_id)
    else:
        profile = await session.scalar(
            select(LLMProfile)
            .where(LLMProfile.enabled.is_(True))
            .order_by(LLMProfile.is_default.desc(), LLMProfile.id)
        )
    if profile is None:
        raise HTTPException(status_code=400, detail="请先添加并启用一个云模型")
    if not profile.enabled:
        raise HTTPException(status_code=400, detail="所选云模型当前未启用")
    return profile.id


@router.get("", response_model=list[TopicOut])
async def list_topics(session: AsyncSession = Depends(get_session)):
    return list(await session.scalars(select(Topic).order_by(Topic.name)))


@router.post(
    "",
    response_model=TopicOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_topic(payload: TopicCreate, session: AsyncSession = Depends(get_session)):
    await _validate_profile(session, payload.llm_profile_id)
    topic = Topic(**payload.model_dump())
    session.add(topic)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A topic with this name already exists"
        ) from exc
    await session.refresh(topic)
    return topic


@router.put("/{topic_id}", response_model=TopicOut, dependencies=[Depends(require_admin)])
async def update_topic(
    topic_id: int,
    payload: TopicUpdate,
    session: AsyncSession = Depends(get_session),
):
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    values = payload.model_dump(exclude_unset=True)
    if "llm_profile_id" in values:
        await _validate_profile(session, values["llm_profile_id"])
    for key, value in values.items():
        setattr(topic, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A topic with this name already exists"
        ) from exc
    await session.refresh(topic)
    return topic


@router.delete(
    "/{topic_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_topic(topic_id: int, session: AsyncSession = Depends(get_session)):
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    await session.delete(topic)
    await session.commit()


@router.post("/actions/preview", dependencies=[Depends(require_admin)])
async def preview_topic(payload: TopicPreviewRequest):
    try:
        papers = await fetch_papers(
            payload.query,
            payload.max_results,
            payload.lookback_days,
            include_cross_list=payload.include_cross_list,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "items": [
            {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": paper.authors,
                "primary_category": paper.primary_category,
                "published_at": paper.published_at,
                "abs_url": paper.abs_url,
            }
            for paper in papers
        ]
    }


@router.post(
    "/actions/suggest-query",
    response_model=TopicQuerySuggestionOut,
    dependencies=[Depends(require_admin)],
)
async def create_query_suggestion(
    payload: TopicQuerySuggestionRequest,
    session: AsyncSession = Depends(get_session),
):
    profile_id = await _resolve_query_profile_id(session, payload.llm_profile_id)
    try:
        routing = await run_with_profile_fallback(
            session,
            profile_id,
            lambda profile: suggest_topic_query(
                profile,
                payload.research_focus,
                payload.categories,
            ),
            honor_cooldown=False,
        )
        return routing.value
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
