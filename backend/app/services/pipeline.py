import asyncio

from filelock import FileLock, Timeout
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    Analysis,
    AnalysisStatus,
    LLMProfile,
    Paper,
    RunLog,
    RunStatus,
    Topic,
    TopicPaper,
    utcnow,
)
from app.services.analysis_service import create_analysis, execute_analysis
from app.services.arxiv import ArxivPaperData, fetch_papers_detailed
from app.services.email_service import send_digest
from app.services.settings_service import get_default_profile_id, get_schedule_settings


class RunAlreadyActive(RuntimeError):
    pass


_run_leases: dict[int, FileLock] = {}
_executing_run_ids: set[int] = set()
_INTERRUPTED_MESSAGE = (
    "Run interrupted by a service restart or unexpected process exit; recovered automatically."
)
_INTERRUPTED_ANALYSIS_MESSAGE = "Analysis interrupted because its daily run stopped."


async def _try_acquire_run_lease() -> FileLock | None:
    path = get_settings().daily_run_lock_path
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    lease = FileLock(str(path), thread_local=False)
    try:
        await asyncio.to_thread(lease.acquire, timeout=0)
    except Timeout:
        return None
    return lease


async def _release_lease(lease: FileLock) -> None:
    await asyncio.to_thread(lease.release)


async def _mark_interrupted_runs(session) -> int:
    runs = list(
        await session.scalars(
            select(RunLog).where(
                RunLog.job_type == "daily",
                RunLog.status == RunStatus.running,
            )
        )
    )
    for run in runs:
        interrupted = await session.execute(
            update(Analysis)
            .where(
                Analysis.run_id == run.id,
                Analysis.status.in_([AnalysisStatus.pending, AnalysisStatus.running]),
            )
            .values(
                status=AnalysisStatus.failed,
                error_message=_INTERRUPTED_ANALYSIS_MESSAGE,
                completed_at=utcnow(),
            )
        )
        run.status = RunStatus.failed
        run.finished_at = utcnow()
        run.analyses_failed += int(interrupted.rowcount or 0)
        run.message = _INTERRUPTED_MESSAGE
        run.error_details = {"errors": [_INTERRUPTED_MESSAGE]}
    return len(runs)


async def recover_interrupted_runs() -> int:
    """Recover orphaned run rows only when no process owns the pipeline lease."""
    lease = await _try_acquire_run_lease()
    if lease is None:
        return 0
    try:
        async with SessionLocal() as session:
            recovered = await _mark_interrupted_runs(session)
            await session.commit()
            return recovered
    finally:
        await _release_lease(lease)


async def start_daily_run(trigger: str = "manual") -> int:
    lease = await _try_acquire_run_lease()
    if lease is None:
        raise RunAlreadyActive("A daily pipeline run is already active")

    lease_retained = False
    try:
        async with SessionLocal() as session:
            await _mark_interrupted_runs(session)
            run = RunLog(job_type="daily", trigger=trigger, status=RunStatus.running)
            session.add(run)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise RunAlreadyActive("A daily pipeline run is already active") from exc
            await session.refresh(run)
            _run_leases[run.id] = lease
            lease_retained = True
            return run.id
    finally:
        if not lease_retained:
            await _release_lease(lease)


async def _upsert_paper(session, data: ArxivPaperData) -> tuple[Paper, bool]:
    paper = await session.scalar(select(Paper).where(Paper.arxiv_id == data.arxiv_id))
    now = utcnow()
    if paper is None:
        paper = Paper(
            arxiv_id=data.arxiv_id,
            version=data.version,
            title=data.title,
            abstract=data.abstract,
            authors=data.authors,
            categories=data.categories,
            primary_category=data.primary_category,
            published_at=data.published_at,
            updated_at=data.updated_at,
            first_seen_at=now,
            last_seen_at=now,
            abs_url=data.abs_url,
            pdf_url=data.pdf_url,
            doi=data.doi,
            journal_ref=data.journal_ref,
            comment=data.comment,
        )
        session.add(paper)
        await session.flush()
        return paper, True

    paper.last_seen_at = now
    if data.version >= paper.version:
        paper.version = data.version
        paper.title = data.title
        paper.abstract = data.abstract
        paper.authors = data.authors
        paper.categories = data.categories
        paper.primary_category = data.primary_category
        paper.published_at = data.published_at
        paper.updated_at = data.updated_at
        paper.abs_url = data.abs_url
        paper.pdf_url = data.pdf_url
        paper.doi = data.doi
        paper.journal_ref = data.journal_ref
        paper.comment = data.comment
    return paper, False


async def _needs_analysis(
    session,
    paper: Paper,
    topic_id: int,
    profile_id: int,
) -> bool:
    latest = await session.scalar(
        select(Analysis)
        .where(
            Analysis.paper_id == paper.id,
            Analysis.topic_id == topic_id,
            Analysis.llm_profile_id == profile_id,
            Analysis.status.in_(
                [AnalysisStatus.pending, AnalysisStatus.running, AnalysisStatus.completed]
            ),
            Analysis.prompt_version == "v1",
        )
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    return latest is None or latest.paper_version < paper.version


async def _current_analysis_id(
    session,
    paper: Paper,
    topic_id: int,
    profile_id: int,
) -> int | None:
    return await session.scalar(
        select(Analysis.id)
        .where(
            Analysis.paper_id == paper.id,
            Analysis.topic_id == topic_id,
            Analysis.llm_profile_id == profile_id,
            Analysis.status == AnalysisStatus.completed,
            Analysis.prompt_version == "v1",
            Analysis.paper_version >= paper.version,
        )
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )


async def _execute_daily_run(
    run_id: int,
    topic_ids: list[int] | None = None,
    send_email: bool = True,
) -> None:
    settings = get_settings()
    errors: list[str] = []
    warnings: list[str] = []
    papers_found = 0
    papers_new = 0
    topics_processed = 0
    skipped_no_profile = 0
    analysis_ids: list[int] = []
    digest_analysis_ids: set[int] = set()
    cached_topics = 0
    degraded_topics = 0
    retry_recommended = False

    try:
        async with SessionLocal() as session:
            schedule = await get_schedule_settings(session)
            default_profile_id = await get_default_profile_id(session)
            statement = select(Topic).where(Topic.enabled.is_(True)).order_by(Topic.name)
            if topic_ids:
                statement = statement.where(Topic.id.in_(topic_ids))
            topics = list(await session.scalars(statement))

        for topic in topics:
            topics_processed += 1
            try:
                fetch_batch = await fetch_papers_detailed(
                    topic.query,
                    topic.max_results,
                    topic.lookback_days,
                    include_cross_list=topic.include_cross_list,
                )
                results = fetch_batch.papers
                if fetch_batch.source != "live":
                    cached_topics += 1
                if fetch_batch.degraded:
                    degraded_topics += 1
                    retry_recommended = retry_recommended or (
                        fetch_batch.retry_recommended and not results
                    )
                if fetch_batch.warning:
                    warnings.append(f"{topic.name}: {fetch_batch.warning}")
                papers_found += len(results)
            except Exception as exc:
                errors.append(f"{topic.name}: arXiv fetch failed: {exc}")
                continue

            async with SessionLocal() as session:
                for data in results:
                    paper, is_new = await _upsert_paper(session, data)
                    papers_new += int(is_new)
                    link = await session.get(
                        TopicPaper, {"topic_id": topic.id, "paper_id": paper.id}
                    )
                    if link is None:
                        session.add(TopicPaper(topic_id=topic.id, paper_id=paper.id))
                    profile_id = topic.llm_profile_id or default_profile_id
                    if profile_id is None:
                        skipped_no_profile += 1
                        continue
                    profile_enabled = await session.scalar(
                        select(LLMProfile.enabled).where(LLMProfile.id == profile_id)
                    )
                    if not profile_enabled:
                        skipped_no_profile += 1
                        continue
                    await session.flush()
                    if await _needs_analysis(session, paper, topic.id, profile_id):
                        await session.commit()
                        analysis_id = await create_analysis(
                            paper.id,
                            topic.id,
                            profile_id,
                            "pdf" if topic.analyze_pdf else "abstract",
                            schedule.digest_language,
                            run_id,
                        )
                        analysis_ids.append(analysis_id)
                    else:
                        current_analysis_id = await _current_analysis_id(
                            session,
                            paper,
                            topic.id,
                            profile_id,
                        )
                        if current_analysis_id is not None:
                            digest_analysis_ids.add(current_analysis_id)
                await session.commit()

        semaphore = asyncio.Semaphore(max(settings.llm_concurrency, 1))

        async def analyze_one(analysis_id: int) -> tuple[int, bool]:
            async with semaphore:
                return analysis_id, await execute_analysis(analysis_id)

        results = await asyncio.gather(
            *(analyze_one(analysis_id) for analysis_id in analysis_ids),
            return_exceptions=True,
        )
        completed_ids: list[int] = []
        analyses_failed = 0
        for result in results:
            if isinstance(result, Exception):
                analyses_failed += 1
                errors.append(f"Analysis worker failed: {result}")
            elif result[1]:
                completed_ids.append(result[0])
            else:
                analyses_failed += 1

        digest_analysis_ids.update(completed_ids)

        emails_sent = 0
        if send_email and digest_analysis_ids:
            try:
                emails_sent = await send_digest(sorted(digest_analysis_ids))
            except Exception as exc:
                errors.append(f"Email delivery failed: {exc}")

        message_parts = [
            f"Processed {topics_processed} topics and found {papers_found} paper matches."
        ]
        if skipped_no_profile:
            message_parts.append(
                f"Skipped {skipped_no_profile} analyses because no enabled cloud model was selected."
            )
        if cached_topics:
            message_parts.append(f"Used non-Atom discovery sources for {cached_topics} topics.")
        if degraded_topics:
            message_parts.append(f"Upstream discovery was degraded for {degraded_topics} topics.")
        if papers_found and not analysis_ids and not skipped_no_profile:
            message_parts.append("All matching papers already had a current analysis; no LLM call was needed.")
        if emails_sent:
            message_parts.append("Email digest sent successfully.")
        status = (
            RunStatus.partial
            if errors or analyses_failed or degraded_topics
            else RunStatus.completed
        )
        async with SessionLocal() as session:
            run = await session.get(RunLog, run_id)
            if run:
                run.status = status
                run.finished_at = utcnow()
                run.topics_processed = topics_processed
                run.papers_found = papers_found
                run.papers_new = papers_new
                run.analyses_completed = len(completed_ids)
                run.analyses_failed = analyses_failed
                run.emails_sent = emails_sent
                run.message = " ".join(message_parts)
                details = {}
                if errors:
                    details["errors"] = errors
                if warnings:
                    details["warnings"] = warnings
                if retry_recommended:
                    details["retry_recommended"] = True
                run.error_details = details or None
                await session.commit()
    except Exception as exc:
        async with SessionLocal() as session:
            run = await session.get(RunLog, run_id)
            if run:
                run.status = RunStatus.failed
                run.finished_at = utcnow()
                run.topics_processed = topics_processed
                run.papers_found = papers_found
                run.papers_new = papers_new
                run.message = "Daily pipeline failed"
                run.error_details = {"errors": [*errors, str(exc)]}
                await session.commit()


async def execute_daily_run(
    run_id: int,
    topic_ids: list[int] | None = None,
    send_email: bool = True,
) -> None:
    if run_id in _executing_run_ids:
        raise RunAlreadyActive(f"Daily pipeline run {run_id} is already executing")

    lease = _run_leases.get(run_id)
    if lease is None:
        lease = await _try_acquire_run_lease()
        if lease is None:
            raise RunAlreadyActive("A daily pipeline run is already active")
        _run_leases[run_id] = lease

    _executing_run_ids.add(run_id)
    try:
        await _execute_daily_run(run_id, topic_ids=topic_ids, send_email=send_email)
    except asyncio.CancelledError:
        async with SessionLocal() as session:
            run = await session.get(RunLog, run_id)
            if run and run.status == RunStatus.running:
                run.status = RunStatus.failed
                run.finished_at = utcnow()
                run.message = _INTERRUPTED_MESSAGE
                run.error_details = {"errors": [_INTERRUPTED_MESSAGE]}
                await session.commit()
        raise
    finally:
        _executing_run_ids.discard(run_id)
        retained_lease = _run_leases.pop(run_id, None)
        if retained_lease is not None:
            await _release_lease(retained_lease)


async def run_daily_pipeline(
    trigger: str = "scheduled",
    topic_ids: list[int] | None = None,
    send_email: bool = True,
) -> int:
    run_id = await start_daily_run(trigger)
    await execute_daily_run(run_id, topic_ids=topic_ids, send_email=send_email)
    return run_id
