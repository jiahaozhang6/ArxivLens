import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.database import SessionLocal, close_db, init_db
from app.models import RunLog, RunStatus
from app.services.network_time import network_clock
from app.services.pipeline import RunAlreadyActive, recover_interrupted_runs, run_daily_pipeline
from app.services.settings_service import ensure_default_settings, get_schedule_settings

logger = logging.getLogger("arxiv-digest-worker")
_schedule_signature: tuple | None = None
_SOURCE_RETRY_DELAYS_MINUTES = (30, 90, 180)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def _scheduled_run_is_due(
    corrected_now: datetime,
    timezone: ZoneInfo,
    hour: int,
    minute: int,
    last_scheduled_at: datetime | None,
    clock_offset_seconds: float,
) -> bool:
    local_now = corrected_now.astimezone(timezone)
    scheduled_today = local_now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if local_now < scheduled_today:
        return False
    if last_scheduled_at is None:
        return True
    corrected_started_at = last_scheduled_at + timedelta(seconds=clock_offset_seconds)
    return corrected_started_at.astimezone(timezone).date() != local_now.date()


def _scheduled_retry_trigger(
    runs: list[RunLog],
    corrected_now: datetime,
    timezone: ZoneInfo,
    clock_offset_seconds: float,
) -> str | None:
    local_today = corrected_now.astimezone(timezone).date()
    todays_runs = [
        run
        for run in runs
        if (run.started_at + timedelta(seconds=clock_offset_seconds))
        .astimezone(timezone)
        .date()
        == local_today
    ]
    if not any(run.trigger == "scheduled" for run in todays_runs):
        return None
    latest = todays_runs[0]
    if latest.status == RunStatus.running:
        return None
    details = latest.error_details or {}
    if latest.status != RunStatus.partial or not details.get("retry_recommended"):
        return None
    retries_completed = sum(run.trigger.startswith("scheduled_retry_") for run in todays_runs)
    if retries_completed >= len(_SOURCE_RETRY_DELAYS_MINUTES):
        return None
    retry_at = (latest.finished_at or latest.started_at) + timedelta(
        seconds=clock_offset_seconds,
        minutes=_SOURCE_RETRY_DELAYS_MINUTES[retries_completed],
    )
    if corrected_now < retry_at:
        return None
    return f"scheduled_retry_{retries_completed + 1}"


async def _scheduled_run(trigger: str = "scheduled") -> None:
    try:
        await run_daily_pipeline(trigger=trigger)
    except RunAlreadyActive:
        logger.info("Skipped scheduled run because another run is active")
    except Exception:
        logger.exception("Scheduled pipeline failed")


async def _check_schedule() -> None:
    global _schedule_signature
    await network_clock.sync()
    corrected_now = network_clock.utcnow()
    snapshot = network_clock.snapshot()
    async with SessionLocal() as session:
        schedule = await get_schedule_settings(session)
        last_scheduled_run = await session.scalar(
            select(RunLog)
            .where(RunLog.trigger == "scheduled")
            .order_by(RunLog.started_at.desc())
            .limit(1)
        )
        recent_scheduled_runs = list(
            await session.scalars(
                select(RunLog)
                .where(RunLog.trigger.like("scheduled%"))
                .order_by(RunLog.started_at.desc())
                .limit(10)
            )
        )
        signature = (schedule.enabled, schedule.timezone, schedule.hour, schedule.minute)
    try:
        timezone = ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError:
        logger.error("Invalid schedule timezone: %s", schedule.timezone)
        return
    if signature != _schedule_signature:
        _schedule_signature = signature
        if schedule.enabled:
            logger.info(
                "Scheduled daily digest at %02d:%02d %s using calibrated time",
                schedule.hour,
                schedule.minute,
                schedule.timezone,
            )
        else:
            logger.info("Daily schedule is disabled")
    if not schedule.enabled:
        return

    if _scheduled_run_is_due(
        corrected_now,
        timezone,
        schedule.hour,
        schedule.minute,
        last_scheduled_run.started_at if last_scheduled_run is not None else None,
        snapshot.offset_seconds,
    ):
        await _scheduled_run()
        return
    retry_trigger = _scheduled_retry_trigger(
        recent_scheduled_runs,
        corrected_now,
        timezone,
        snapshot.offset_seconds,
    )
    if retry_trigger is not None:
        logger.warning("Retrying degraded scheduled discovery with %s", retry_trigger)
        await _scheduled_run(retry_trigger)


async def run_worker() -> None:
    await init_db()
    _configure_logging()
    recovered = await recover_interrupted_runs()
    if recovered:
        logger.warning("Recovered %d interrupted daily run(s)", recovered)
    async with SessionLocal() as session:
        await ensure_default_settings(session)
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _check_schedule,
        IntervalTrigger(seconds=30),
        id="check-schedule",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    await _check_schedule()
    scheduler.start()
    logger.info("Worker started")
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await close_db()


def main() -> None:
    _configure_logging()
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
