import asyncio
import math
from datetime import UTC, datetime, time
from datetime import date as Date
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_admin
from app.config import get_settings
from app.database import get_session
from app.models import Analysis, AnalysisStatus, LLMProfile, Paper, PaperDecision, Topic, TopicPaper
from app.schemas import (
    AnalysisOut,
    BulkReanalyzeRequest,
    PaperBulkActionRequest,
    PaperUpdate,
    ReanalyzeRequest,
)
from app.services.analysis_service import create_analysis, execute_analysis
from app.services.settings_service import get_default_profile_id, get_schedule_settings

router = APIRouter(
    prefix="/api/papers",
    tags=["papers"],
    dependencies=[Depends(require_admin)],
)
_analysis_tasks: set[asyncio.Task] = set()


def _retain_task(task: asyncio.Task) -> None:
    _analysis_tasks.add(task)
    task.add_done_callback(_analysis_tasks.discard)


async def _execute_analysis_batch(analysis_ids: list[int]) -> None:
    semaphore = asyncio.Semaphore(max(get_settings().llm_concurrency, 1))

    async def execute_one(analysis_id: int) -> bool:
        async with semaphore:
            return await execute_analysis(analysis_id)

    await asyncio.gather(*(execute_one(analysis_id) for analysis_id in analysis_ids))


def _latest_analysis(paper: Paper) -> Analysis | None:
    return max(paper.analyses, key=lambda item: item.id, default=None)


def _analysis_dict(analysis: Analysis | None):
    return AnalysisOut.model_validate(analysis) if analysis else None


def _paper_dict(paper: Paper, detailed: bool = False) -> dict:
    topics = sorted(
        (
            {
                "id": link.topic.id,
                "name": link.topic.name,
                "discovered_at": link.discovered_at,
            }
            for link in paper.topic_links
        ),
        key=lambda item: item["name"],
    )
    result = {
        "id": paper.id,
        "arxiv_id": paper.arxiv_id,
        "version": paper.version,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": paper.authors,
        "categories": paper.categories,
        "primary_category": paper.primary_category,
        "published_at": paper.published_at,
        "updated_at": paper.updated_at,
        "first_seen_at": paper.first_seen_at,
        "abs_url": paper.abs_url,
        "pdf_url": paper.pdf_url,
        "doi": paper.doi,
        "journal_ref": paper.journal_ref,
        "comment": paper.comment,
        "is_read": paper.is_read,
        "is_starred": paper.is_starred,
        "decision": paper.decision,
        "personal_notes": paper.personal_notes,
        "user_tags": paper.user_tags,
        "topics": topics,
        "latest_analysis": _analysis_dict(_latest_analysis(paper)),
        "analysis_count": len(paper.analyses),
    }
    if detailed:
        result["analyses"] = [
            AnalysisOut.model_validate(item)
            for item in sorted(paper.analyses, key=lambda item: item.id, reverse=True)
        ]
    return result


def _utc_bounds(day: Date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day, time.max, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def _analysis_exists(status_value: AnalysisStatus):
    return exists(
        select(Analysis.id).where(
            Analysis.paper_id == Paper.id,
            Analysis.status == status_value,
        )
    )


@router.get("")
async def list_papers(
    day: Date | None = None,
    topic_id: int | None = None,
    q: str | None = Query(default=None, max_length=300),
    state: str = Query(default="all"),
    analysis_status: str = Query(default="all"),
    sort: str = Query(default="relevance"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    schedule = await get_schedule_settings(session)
    link_conditions = []
    if day:
        start, end = _utc_bounds(day, schedule.timezone)
        link_conditions.extend([TopicPaper.discovered_at >= start, TopicPaper.discovered_at <= end])
    if topic_id:
        link_conditions.append(TopicPaper.topic_id == topic_id)

    paper_ids = select(TopicPaper.paper_id).where(*link_conditions).distinct()
    conditions = [Paper.id.in_(paper_ids)]
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(
            or_(
                Paper.title.ilike(pattern),
                Paper.abstract.ilike(pattern),
                Paper.arxiv_id.ilike(pattern),
            )
        )
    if state == "unread":
        conditions.append(Paper.is_read.is_(False))
    elif state == "read":
        conditions.append(Paper.is_read.is_(True))
    elif state == "starred":
        conditions.append(Paper.is_starred.is_(True))
    elif state in {item.value for item in PaperDecision}:
        conditions.append(Paper.decision == state)

    completed_exists = _analysis_exists(AnalysisStatus.completed)
    failed_exists = _analysis_exists(AnalysisStatus.failed)
    if analysis_status == "completed":
        conditions.append(completed_exists)
    elif analysis_status == "missing":
        conditions.append(~completed_exists)
    elif analysis_status == "failed":
        conditions.extend([failed_exists, ~completed_exists])

    total = await session.scalar(select(func.count()).select_from(Paper).where(*conditions)) or 0
    relevance = (
        select(func.max(Analysis.relevance_score))
        .where(
            Analysis.paper_id == Paper.id,
            Analysis.status == AnalysisStatus.completed,
        )
        .correlate(Paper)
        .scalar_subquery()
    )
    statement = (
        select(Paper)
        .where(*conditions)
        .options(
            selectinload(Paper.topic_links).joinedload(TopicPaper.topic),
            selectinload(Paper.analyses),
        )
    )
    if sort == "published":
        statement = statement.order_by(Paper.published_at.desc(), Paper.id.desc())
    elif sort == "title":
        statement = statement.order_by(Paper.title.asc(), Paper.id.desc())
    else:
        statement = statement.order_by(
            relevance.desc().nullslast(), Paper.published_at.desc(), Paper.id.desc()
        )
    papers = list(await session.scalars(statement.offset((page - 1) * page_size).limit(page_size)))

    base_conditions = [Paper.id.in_(paper_ids)]
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        base_conditions.append(
            or_(
                Paper.title.ilike(pattern),
                Paper.abstract.ilike(pattern),
                Paper.arxiv_id.ilike(pattern),
            )
        )

    async def count_where(*extra) -> int:
        return (
            await session.scalar(
                select(func.count()).select_from(Paper).where(*base_conditions, *extra)
            )
            or 0
        )

    stats = {
        "total": await count_where(),
        "unread": await count_where(Paper.is_read.is_(False)),
        "starred": await count_where(Paper.is_starred.is_(True)),
        "relevant": await count_where(Paper.decision == PaperDecision.relevant),
        "analyzed": await count_where(completed_exists),
    }
    return {
        "items": [_paper_dict(paper) for paper in papers],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(math.ceil(total / page_size), 1),
        "stats": stats,
    }


@router.get("/dates")
async def list_dates(
    topic_id: int | None = None,
    limit: int = Query(default=180, ge=1, le=730),
    session: AsyncSession = Depends(get_session),
):
    schedule = await get_schedule_settings(session)
    statement = select(TopicPaper.discovered_at, TopicPaper.paper_id).order_by(
        TopicPaper.discovered_at.desc()
    ).limit(10000)
    if topic_id:
        statement = statement.where(TopicPaper.topic_id == topic_id)
    records = (await session.execute(statement)).all()
    zone = ZoneInfo(schedule.timezone)
    paper_ids_by_date: dict[str, set[int]] = {}
    for value, paper_id in records:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        label = value.astimezone(zone).date().isoformat()
        paper_ids_by_date.setdefault(label, set()).add(paper_id)
    dates = list(paper_ids_by_date)[:limit]
    return {
        "dates": dates,
        "items": [{"date": value, "count": len(paper_ids_by_date[value])} for value in dates],
    }


@router.post("/actions/bulk")
async def bulk_manage_papers(
    payload: PaperBulkActionRequest,
    session: AsyncSession = Depends(get_session),
):
    existing_ids = set(
        await session.scalars(select(Paper.id).where(Paper.id.in_(payload.paper_ids)))
    )
    if not existing_ids:
        raise HTTPException(status_code=404, detail="No matching papers found")

    if payload.action == "delete":
        papers = list(await session.scalars(select(Paper).where(Paper.id.in_(existing_ids))))
        for paper in papers:
            await session.delete(paper)
    else:
        values: dict = {}
        if payload.action == "mark_read":
            values["is_read"] = True
        elif payload.action == "mark_unread":
            values["is_read"] = False
        elif payload.action == "star":
            values["is_starred"] = True
        elif payload.action == "unstar":
            values["is_starred"] = False
        else:
            values["decision"] = PaperDecision(payload.action)
        await session.execute(update(Paper).where(Paper.id.in_(existing_ids)).values(**values))

    await session.commit()
    return {
        "ok": True,
        "action": payload.action,
        "affected": len(existing_ids),
        "missing_ids": [paper_id for paper_id in payload.paper_ids if paper_id not in existing_ids],
    }


@router.post("/actions/analyze", status_code=status.HTTP_202_ACCEPTED)
async def bulk_reanalyze_papers(
    payload: BulkReanalyzeRequest,
    session: AsyncSession = Depends(get_session),
):
    if payload.llm_profile_id is not None:
        selected_profile = await session.get(LLMProfile, payload.llm_profile_id)
        if selected_profile is None or not selected_profile.enabled:
            raise HTTPException(status_code=400, detail="Select an enabled cloud model profile first")

    papers = list(
        await session.scalars(
            select(Paper)
            .where(Paper.id.in_(payload.paper_ids))
            .options(selectinload(Paper.topic_links).joinedload(TopicPaper.topic))
            .order_by(Paper.id)
        )
    )
    if not papers:
        raise HTTPException(status_code=404, detail="No matching papers found")

    schedule = await get_schedule_settings(session)
    default_profile_id = await get_default_profile_id(session)
    enabled_profile_ids = set(
        await session.scalars(select(LLMProfile.id).where(LLMProfile.enabled.is_(True)))
    )
    analysis_ids: list[int] = []
    skipped_ids: list[int] = []
    for paper in papers:
        topic = paper.topic_links[0].topic if paper.topic_links else None
        profile_id = payload.llm_profile_id or (topic.llm_profile_id if topic else None)
        if profile_id not in enabled_profile_ids:
            profile_id = default_profile_id
        if profile_id is None or profile_id not in enabled_profile_ids:
            skipped_ids.append(paper.id)
            continue
        source_mode = payload.source_mode or (
            "pdf" if topic is not None and topic.analyze_pdf else "abstract"
        )
        analysis_ids.append(
            await create_analysis(
                paper.id,
                topic.id if topic else None,
                profile_id,
                source_mode,
                schedule.digest_language,
            )
        )

    if analysis_ids:
        task = asyncio.create_task(
            _execute_analysis_batch(analysis_ids),
            name=f"bulk-paper-analysis-{analysis_ids[0]}",
        )
        _retain_task(task)
    return {
        "analysis_ids": analysis_ids,
        "submitted": len(analysis_ids),
        "skipped_paper_ids": skipped_ids,
    }


@router.get("/{paper_id}")
async def get_paper(paper_id: int, session: AsyncSession = Depends(get_session)):
    paper = await session.scalar(
        select(Paper)
        .where(Paper.id == paper_id)
        .options(
            selectinload(Paper.topic_links).joinedload(TopicPaper.topic),
            selectinload(Paper.analyses),
        )
    )
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return _paper_dict(paper, detailed=True)


@router.patch("/{paper_id}")
async def update_paper(
    paper_id: int,
    payload: PaperUpdate,
    session: AsyncSession = Depends(get_session),
):
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    values = payload.model_dump(exclude_unset=True)
    if values.get("user_tags") is not None:
        values["user_tags"] = list(
            dict.fromkeys(tag.strip()[:80] for tag in values["user_tags"] if tag.strip())
        )[:30]
    for key, value in values.items():
        setattr(paper, key, value)
    await session.commit()
    return {"ok": True}


@router.post("/{paper_id}/actions/analyze", status_code=status.HTTP_202_ACCEPTED)
async def reanalyze_paper(
    paper_id: int,
    payload: ReanalyzeRequest,
    session: AsyncSession = Depends(get_session),
):
    paper = await session.scalar(
        select(Paper)
        .where(Paper.id == paper_id)
        .options(selectinload(Paper.topic_links).joinedload(TopicPaper.topic))
    )
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    topic = None
    if payload.topic_id:
        topic = await session.get(Topic, payload.topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
    elif paper.topic_links:
        topic = paper.topic_links[0].topic
    profile_id = payload.llm_profile_id or (topic.llm_profile_id if topic else None)
    profile_id = profile_id or await get_default_profile_id(session)
    if profile_id is None or await session.get(LLMProfile, profile_id) is None:
        raise HTTPException(status_code=400, detail="Select an enabled cloud model profile first")
    schedule = await get_schedule_settings(session)
    source_mode = payload.source_mode or ("pdf" if topic and topic.analyze_pdf else "abstract")
    analysis_id = await create_analysis(
        paper.id,
        topic.id if topic else None,
        profile_id,
        source_mode,
        schedule.digest_language,
    )
    task = asyncio.create_task(execute_analysis(analysis_id), name=f"paper-analysis-{analysis_id}")
    _retain_task(task)
    return {"analysis_id": analysis_id, "status": "pending"}


@router.get("/{paper_id}/export/markdown", response_class=PlainTextResponse)
async def export_markdown(paper_id: int, session: AsyncSession = Depends(get_session)):
    paper = await session.scalar(
        select(Paper).where(Paper.id == paper_id).options(selectinload(Paper.analyses))
    )
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    analysis = _latest_analysis(paper)
    lines = [
        f"# {paper.title}",
        "",
        f"- arXiv: [{paper.arxiv_id}]({paper.abs_url})",
        f"- Authors: {', '.join(paper.authors)}",
        f"- Categories: {', '.join(paper.categories)}",
        f"- Published: {paper.published_at.date().isoformat()}",
        "",
        "## Abstract",
        paper.abstract,
    ]
    if analysis:
        lines.extend(
            [
                "",
                "## LLM Analysis",
                analysis.summary or "",
                "",
                "### Research question",
                analysis.research_question or "",
                "",
                "### Contributions",
                *[f"- {item}" for item in analysis.contributions or []],
                "",
                "### Methodology",
                analysis.methodology or "",
                "",
                "### Experiments",
                analysis.experiments or "",
                "",
                "### Limitations",
                *[f"- {item}" for item in analysis.limitations or []],
                "",
                "### Reading advice",
                analysis.reading_advice or "",
            ]
        )
    if paper.personal_notes:
        lines.extend(["", "## Personal notes", paper.personal_notes])
    return "\n".join(lines)
