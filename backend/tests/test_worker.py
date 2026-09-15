import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import RunLog, RunStatus
from app.worker import (
    _active_pipeline_tasks,
    _scheduled_retry_trigger,
    _scheduled_run_is_due,
    _wait_for_active_pipeline_tasks,
)


def test_schedule_waits_until_configured_local_time():
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 12, 22, 30, tzinfo=UTC)

    assert not _scheduled_run_is_due(now, timezone, 7, 0, None, 0)


def test_schedule_catches_up_any_time_after_configured_time():
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 13, 7, 30, tzinfo=UTC)
    yesterday = datetime(2026, 9, 11, 23, 0, tzinfo=UTC)

    assert _scheduled_run_is_due(now, timezone, 7, 0, None, 0)
    assert _scheduled_run_is_due(now, timezone, 7, 0, yesterday, 0)


def test_schedule_runs_only_once_per_local_day():
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 13, 7, 30, tzinfo=UTC)
    today = datetime(2026, 9, 12, 23, 0, tzinfo=UTC)

    assert not _scheduled_run_is_due(now, timezone, 7, 0, today, 0)


def test_degraded_scheduled_run_retries_after_10_minutes():
    timezone = ZoneInfo("Asia/Shanghai")
    started = datetime(2026, 9, 14, 5, 0, tzinfo=UTC)
    run = RunLog(
        trigger="scheduled",
        status=RunStatus.partial,
        started_at=started,
        finished_at=started,
        error_details={"retry_recommended": True},
    )

    assert _scheduled_retry_trigger(
        [run],
        datetime(2026, 9, 14, 5, 9, tzinfo=UTC),
        timezone,
        0,
    ) is None
    assert _scheduled_retry_trigger(
        [run],
        datetime(2026, 9, 14, 5, 10, tzinfo=UTC),
        timezone,
        0,
    ) == "scheduled_retry_1"


def test_failed_scheduled_run_retries_and_completed_run_does_not():
    timezone = ZoneInfo("Asia/Shanghai")
    started = datetime(2026, 9, 15, 5, 0, tzinfo=UTC)
    failed = RunLog(
        trigger="scheduled",
        status=RunStatus.failed,
        started_at=started,
        finished_at=started,
        analyses_failed=0,
    )
    completed = RunLog(
        trigger="scheduled",
        status=RunStatus.completed,
        started_at=started,
        finished_at=started,
        analyses_failed=0,
    )

    now = datetime(2026, 9, 15, 5, 10, tzinfo=UTC)
    assert _scheduled_retry_trigger([failed], now, timezone, 0) == "scheduled_retry_1"
    assert _scheduled_retry_trigger([completed], now, timezone, 0) is None


def test_scheduled_retry_stops_after_three_attempts():
    timezone = ZoneInfo("Asia/Shanghai")
    started = datetime(2026, 9, 15, 5, 0, tzinfo=UTC)
    runs = [
        RunLog(
            trigger=f"scheduled_retry_{number}",
            status=RunStatus.failed,
            started_at=started + timedelta(minutes=number),
            finished_at=started + timedelta(minutes=number),
            analyses_failed=1,
        )
        for number in (3, 2, 1)
    ]
    runs.append(
        RunLog(
            trigger="scheduled",
            status=RunStatus.failed,
            started_at=started,
            finished_at=started,
            analyses_failed=1,
        )
    )

    assert (
        _scheduled_retry_trigger(
            runs,
            datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
            timezone,
            0,
        )
        is None
    )


async def test_worker_shutdown_waits_for_active_pipeline_task():
    _active_pipeline_tasks.clear()
    release = asyncio.Event()

    async def active_work() -> None:
        await release.wait()

    task = asyncio.create_task(active_work())
    _active_pipeline_tasks.add(task)
    task.add_done_callback(_active_pipeline_tasks.discard)
    waiter = asyncio.create_task(_wait_for_active_pipeline_tasks())
    await asyncio.sleep(0)
    assert not waiter.done()

    release.set()
    await waiter
    assert task.done()
    assert not _active_pipeline_tasks
