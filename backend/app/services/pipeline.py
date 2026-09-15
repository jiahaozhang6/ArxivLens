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
from app.services.email_service import send_digest, send_run_failure_alert
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


async def _update_run_progress(
    run_id: int,
    stage: str,
    current: int,
    total: int,
    percent: int,
    **counters: int,
) -> None:
    async with SessionLocal() as session:
        run = await session.get(RunLog, run_id)
        if run is None:
            return
        run.progress_stage = stage
        run.progress_current = max(current, 0)
        run.progress_total = max(total, 0)
        run.progress_percent = min(max(percent, 0), 100)
        for name, value in counters.items():
            setattr(run, name, value)
        await session.commit()


async def _notify_run_failure(run_id: int, trigger: str) -> int:
    if trigger == "scheduled":
        kind = "retrying"
    elif trigger == "scheduled_retry_3":
        kind = "final"
    elif trigger == "manual":
        kind = "failure"
    else:
        return 0

    try:
        sent = await send_run_failure_alert(run_id, kind=kind)
    except Exception as exc:
        async with SessionLocal() as session:
            run = await session.get(RunLog, run_id)
            if run is not None:
                details = dict(run.error_details or {})
                warnings = list(details.get("warnings", []))
                warnings.append(f"Failure notification email could not be sent: {exc}")
                details["warnings"] = warnings
                run.error_details = details
                await session.commit()
        return 0

    if sent:
        async with SessionLocal() as session:
            run = await session.get(RunLog, run_id)
            if run is not None:
                run.emails_sent += sent
                await session.commit()
    return sent


async def _mark_interrupted_runs(session) -> list[tuple[int, str]]:
    runs = list(
        await session.scalars(
            select(RunLog).where(
                RunLog.job_type == "daily",
                RunLog.status == RunStatus.running,
            )
        )
    )
    recovered: list[tuple[int, str]] = []
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
        run.progress_stage = "failed"
        run.finished_at = utcnow()
        run.analyses_failed += int(interrupted.rowcount or 0)
        run.message = _INTERRUPTED_MESSAGE
        run.error_details = {
            "errors": [_INTERRUPTED_MESSAGE],
            "retry_recommended": True,
        }
        recovered.append((run.id, run.trigger))
    return recovered


async def recover_interrupted_runs() -> int:
    """Recover orphaned run rows only when no process owns the pipeline lease."""
    lease = await _try_acquire_run_lease()
    if lease is None:
        return 0
    try:
        async with SessionLocal() as session:
            recovered = await _mark_interrupted_runs(session)
            await session.commit()
        for run_id, trigger in recovered:
            await _notify_run_failure(run_id, trigger)
        return len(recovered)
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
            run = RunLog(
                job_type="daily",
                trigger=trigger,
                status=RunStatus.running,
                progress_stage="starting",
            )
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
    analyses_failed = 0
    completed_ids: list[int] = []
    emails_sent = 0
    trigger = "manual"

    try:
        async with SessionLocal() as session:
            run = await session.get(RunLog, run_id)
            if run is not None:
                trigger = run.trigger
            schedule = await get_schedule_settings(session)
            default_profile_id = await get_default_profile_id(session)
            statement = select(Topic).where(Topic.enabled.is_(True)).order_by(Topic.name)
            if topic_ids:
                statement = statement.where(Topic.id.in_(topic_ids))
            topics = list(await session.scalars(statement))

        await _update_run_progress(run_id, "fetching", 0, len(topics), 5)
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
                retry_recommended = True
                await _update_run_progress(
                    run_id,
                    "fetching",
                    topics_processed,
                    len(topics),
                    5 + round(40 * topics_processed / max(len(topics), 1)),
                    topics_processed=topics_processed,
                    papers_found=papers_found,
                    papers_new=papers_new,
                )
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

            await _update_run_progress(
                run_id,
                "fetching",
                topics_processed,
                len(topics),
                5 + round(40 * topics_processed / max(len(topics), 1)),
                topics_processed=topics_processed,
                papers_found=papers_found,
                papers_new=papers_new,
            )

        await _update_run_progress(
            run_id,
            "analyzing",
            0,
            len(analysis_ids),
            45 if analysis_ids else 90,
            analyses_completed=0,
            analyses_failed=0,
        )
        semaphore = asyncio.Semaphore(max(settings.llm_concurrency, 1))

        async def analyze_one(analysis_id: int) -> tuple[int, bool, str | None]:
            async with semaphore:
                try:
                    return analysis_id, await execute_analysis(analysis_id), None
                except Exception as exc:
                    return analysis_id, False, str(exc)

        analysis_tasks = [
            asyncio.create_task(analyze_one(analysis_id), name=f"daily-analysis-{analysis_id}")
            for analysis_id in analysis_ids
        ]
        try:
            for completed_count, task in enumerate(
                asyncio.as_completed(analysis_tasks),
                start=1,
            ):
                analysis_id, succeeded, error = await task
                if succeeded:
                    completed_ids.append(analysis_id)
                else:
                    analyses_failed += 1
                    retry_recommended = True
                    if error:
                        errors.append(f"Analysis {analysis_id} failed: {error}")
                await _update_run_progress(
                    run_id,
                    "analyzing",
                    completed_count,
                    len(analysis_ids),
                    45 + round(45 * completed_count / max(len(analysis_ids), 1)),
                    analyses_completed=len(completed_ids),
                    analyses_failed=analyses_failed,
                )
        except BaseException:
            for task in analysis_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*analysis_tasks, return_exceptions=True)
            raise

        digest_analysis_ids.update(completed_ids)

        if send_email and digest_analysis_ids:
            await _update_run_progress(run_id, "emailing", 0, 1, 92)
            try:
                emails_sent = await send_digest(sorted(digest_analysis_ids))
            except Exception as exc:
                errors.append(f"Email delivery failed: {exc}")
                retry_recommended = True
            await _update_run_progress(
                run_id,
                "emailing",
                1,
                1,
                98,
                emails_sent=emails_sent,
            )

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
            message_parts.append(
                "All matching papers already had a current analysis; no LLM call was needed."
            )
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
                run.progress_stage = "completed"
                run.progress_current = 1
                run.progress_total = 1
                run.progress_percent = 100
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
                if retry_recommended or errors or analyses_failed:
                    details["retry_recommended"] = True
                run.error_details = details or None
                await session.commit()
        if retry_recommended or errors or analyses_failed:
            await _notify_run_failure(run_id, trigger)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        async with SessionLocal() as session:
            run = await session.get(RunLog, run_id)
            if run:
                trigger = run.trigger
                run.status = RunStatus.failed
                run.progress_stage = "failed"
                run.finished_at = utcnow()
                run.topics_processed = topics_processed
                run.papers_found = papers_found
                run.papers_new = papers_new
                run.analyses_completed = len(completed_ids)
                run.analyses_failed = analyses_failed
                run.emails_sent = emails_sent
                run.message = "Daily pipeline failed"
                run.error_details = {
                    "errors": [*errors, str(exc)],
                    "warnings": warnings,
                    "retry_recommended": True,
                }
                await session.commit()
        await _notify_run_failure(run_id, trigger)


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
                trigger = run.trigger
                run.status = RunStatus.failed
                run.progress_stage = "failed"
                run.finished_at = utcnow()
                run.message = _INTERRUPTED_MESSAGE
                run.error_details = {
                    "errors": [_INTERRUPTED_MESSAGE],
                    "retry_recommended": True,
                }
                await session.commit()
        await _notify_run_failure(run_id, trigger)
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
