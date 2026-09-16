import pytest
from filelock import FileLock
from sqlalchemy import delete, select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import (
    Analysis,
    AnalysisStatus,
    LLMProfile,
    Paper,
    ProtocolType,
    RunLog,
    RunStatus,
    Topic,
    utcnow,
)
from app.services import pipeline
from app.services.arxiv import ArxivFetchBatch, ArxivPaperData

pytestmark = pytest.mark.asyncio


async def _clear_daily_runs() -> None:
    await init_db()
    async with SessionLocal() as session:
        await session.execute(delete(RunLog).where(RunLog.job_type == "daily"))
        await session.commit()


async def _create_running_run() -> int:
    async with SessionLocal() as session:
        run = RunLog(job_type="daily", trigger="manual", status=RunStatus.running)
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run.id


async def test_recover_interrupted_run_when_process_lease_is_free():
    await _clear_daily_runs()
    run_id = await _create_running_run()
    async with SessionLocal() as session:
        now = utcnow()
        paper = Paper(
            arxiv_id=f"lease-test-{run_id}",
            title="Lease recovery test",
            abstract="Test abstract",
            authors=[],
            categories=["cs.AI"],
            published_at=now,
            updated_at=now,
            abs_url="https://arxiv.org/abs/lease-test",
            pdf_url="https://arxiv.org/pdf/lease-test",
        )
        session.add(paper)
        await session.flush()
        analysis = Analysis(
            paper_id=paper.id,
            run_id=run_id,
            provider="test",
            model="test-model",
            status=AnalysisStatus.running,
        )
        session.add(analysis)
        await session.commit()
        await session.refresh(analysis)
        analysis_id = analysis.id

    recovered = await pipeline.recover_interrupted_runs()

    assert recovered == 1
    async with SessionLocal() as session:
        run = await session.get(RunLog, run_id)
        assert run is not None
        assert run.status == RunStatus.failed
        assert run.analyses_failed == 1
        assert "recovered automatically" in (run.message or "")
        analysis = await session.get(Analysis, analysis_id)
        assert analysis is not None
        assert analysis.status == AnalysisStatus.failed
        assert "daily run stopped" in (analysis.error_message or "")


async def test_recovery_preserves_run_owned_by_live_process():
    await _clear_daily_runs()
    run_id = await _create_running_run()
    lease = FileLock(str(get_settings().daily_run_lock_path))
    lease.acquire(timeout=0)
    try:
        recovered = await pipeline.recover_interrupted_runs()

        assert recovered == 0
        async with SessionLocal() as session:
            status = await session.scalar(select(RunLog.status).where(RunLog.id == run_id))
            assert status == RunStatus.running
    finally:
        lease.release()
        await _clear_daily_runs()


async def test_start_run_replaces_orphan_and_holds_exclusive_lease():
    await _clear_daily_runs()
    orphan_id = await _create_running_run()
    run_id = await pipeline.start_daily_run(trigger="manual")
    try:
        async with SessionLocal() as session:
            orphan = await session.get(RunLog, orphan_id)
            current = await session.get(RunLog, run_id)
            assert orphan is not None and orphan.status == RunStatus.failed
            assert current is not None and current.status == RunStatus.running

        try:
            await pipeline.start_daily_run(trigger="manual")
        except pipeline.RunAlreadyActive:
            pass
        else:
            raise AssertionError("a second daily run acquired the live process lease")
    finally:
        retained_lease = pipeline._run_leases.pop(run_id, None)
        if retained_lease is not None:
            await pipeline._release_lease(retained_lease)
        await _clear_daily_runs()


async def test_daily_run_sends_notice_when_no_new_paper_is_found(monkeypatch):
    await _clear_daily_runs()
    now = utcnow()
    async with SessionLocal() as session:
        profile = LLMProfile(
            name=f"pipeline-email-profile-{now.timestamp()}",
            protocol=ProtocolType.openai_compatible,
            base_url="https://example.com/v1",
            model="test-model",
            enabled=True,
        )
        session.add(profile)
        await session.flush()
        topic = Topic(
            name=f"pipeline-email-topic-{now.timestamp()}",
            query="all:pipeline-email-test",
            enabled=True,
            max_results=1,
            lookback_days=4,
            llm_profile_id=profile.id,
        )
        paper = Paper(
            arxiv_id=f"pipeline-email-{now.timestamp()}",
            version=1,
            title="Existing analyzed paper",
            abstract="Test abstract",
            authors=["Test Author"],
            categories=["cs.AI"],
            primary_category="cs.AI",
            published_at=now,
            updated_at=now,
            abs_url="https://arxiv.org/abs/pipeline-email",
            pdf_url="https://arxiv.org/pdf/pipeline-email",
        )
        session.add_all([topic, paper])
        await session.flush()
        analysis = Analysis(
            paper_id=paper.id,
            topic_id=topic.id,
            llm_profile_id=profile.id,
            provider="test",
            model="test-model",
            paper_version=1,
            prompt_version="v1",
            status=AnalysisStatus.completed,
            summary="Already complete",
            completed_at=now,
        )
        session.add(analysis)
        await session.commit()
        await session.refresh(analysis)
        topic_id = topic.id
        paper_id = paper.id
        profile_id = profile.id

    paper_data = ArxivPaperData(
        arxiv_id=paper.arxiv_id,
        version=1,
        title=paper.title,
        abstract=paper.abstract,
        authors=paper.authors,
        categories=paper.categories,
        primary_category=paper.primary_category,
        published_at=paper.published_at,
        updated_at=paper.updated_at,
        abs_url=paper.abs_url,
        pdf_url=paper.pdf_url,
        doi=None,
        journal_ref=None,
        comment=None,
    )
    emailed_ids: list[int] = []
    no_new_notices: list[tuple[int, int, int]] = []

    async def fake_fetch(*_args, **_kwargs):
        return ArxivFetchBatch(papers=[paper_data], source="cache")

    async def fake_send_digest(analysis_ids: list[int]) -> int:
        emailed_ids.extend(analysis_ids)
        return 1

    async def fake_send_no_new_notice(
        run_id: int,
        topics_processed: int,
        papers_found: int,
    ) -> int:
        no_new_notices.append((run_id, topics_processed, papers_found))
        return 1

    progress_stages: list[str] = []
    original_update_progress = pipeline._update_run_progress

    async def capture_progress(run_id: int, stage: str, current: int, total: int, percent: int, **counters):
        progress_stages.append(stage)
        await original_update_progress(run_id, stage, current, total, percent, **counters)

    monkeypatch.setattr(pipeline, "fetch_papers_detailed", fake_fetch)
    monkeypatch.setattr(pipeline, "send_digest", fake_send_digest)
    monkeypatch.setattr(pipeline, "send_no_new_papers_notice", fake_send_no_new_notice)
    monkeypatch.setattr(pipeline, "_update_run_progress", capture_progress)

    run_id = await pipeline.start_daily_run(trigger="scheduled")
    try:
        await pipeline.execute_daily_run(run_id, topic_ids=[topic_id], send_email=True)

        assert emailed_ids == []
        assert no_new_notices == [(run_id, 1, 1)]
        async with SessionLocal() as session:
            run = await session.get(RunLog, run_id)
            assert run is not None
            assert run.status == RunStatus.completed
            assert run.analyses_completed == 0
            assert run.emails_sent == 1
            assert "No-new-paper notification" in (run.message or "")
            assert run.progress_stage == "completed"
            assert run.progress_percent == 100
            assert progress_stages == ["fetching", "fetching", "analyzing", "emailing", "emailing"]
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(Paper).where(Paper.id == paper_id))
            await session.execute(delete(Topic).where(Topic.id == topic_id))
            await session.execute(delete(LLMProfile).where(LLMProfile.id == profile_id))
            await session.execute(delete(RunLog).where(RunLog.id == run_id))
            await session.commit()


async def test_failure_alerts_are_sent_only_for_initial_manual_and_final_runs(monkeypatch):
    await _clear_daily_runs()
    sent: list[tuple[int, str]] = []

    async def fake_alert(run_id: int, kind: str = "failure") -> int:
        sent.append((run_id, kind))
        return 2

    monkeypatch.setattr(pipeline, "send_run_failure_alert", fake_alert)
    async with SessionLocal() as session:
        runs = [
            RunLog(job_type="daily", trigger="scheduled", status=RunStatus.failed),
            RunLog(job_type="daily", trigger="scheduled_retry_1", status=RunStatus.failed),
            RunLog(job_type="daily", trigger="scheduled_retry_3", status=RunStatus.failed),
            RunLog(job_type="daily", trigger="manual", status=RunStatus.failed),
        ]
        session.add_all(runs)
        await session.commit()
        run_ids = [run.id for run in runs]

    assert await pipeline._notify_run_failure(run_ids[0], "scheduled") == 2
    assert await pipeline._notify_run_failure(run_ids[1], "scheduled_retry_1") == 0
    assert await pipeline._notify_run_failure(run_ids[2], "scheduled_retry_3") == 2
    assert await pipeline._notify_run_failure(run_ids[3], "manual") == 2
    assert sent == [
        (run_ids[0], "retrying"),
        (run_ids[2], "final"),
        (run_ids[3], "failure"),
    ]

    async with SessionLocal() as session:
        email_counts = list(
            await session.scalars(
                select(RunLog.emails_sent)
                .where(RunLog.id.in_(run_ids))
                .order_by(RunLog.id)
            )
        )
        assert email_counts == [2, 0, 2, 2]
        await session.execute(delete(RunLog).where(RunLog.id.in_(run_ids)))
        await session.commit()
