from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_session
from app.models import LLMProfile, RunLog, RunStatus, Topic
from app.schemas import RunLogOut
from app.services.network_time import network_clock, next_daily_run_utc
from app.services.settings_service import ensure_default_settings, get_default_profile_id

router = APIRouter(
    prefix="/api/system",
    tags=["system"],
    dependencies=[Depends(require_admin)],
)


def _time_status(schedule):
    snapshot = network_clock.snapshot()
    current_time = network_clock.utcnow()
    return {
        "status": snapshot.status,
        "synchronized": snapshot.synchronized,
        "source": snapshot.source,
        "current_time": current_time,
        "system_time": datetime.now(UTC),
        "offset_seconds": round(snapshot.offset_seconds, 3),
        "round_trip_ms": (
            round(snapshot.round_trip_ms, 1) if snapshot.round_trip_ms is not None else None
        ),
        "synchronized_at": snapshot.synchronized_at,
        "last_attempt_at": snapshot.last_attempt_at,
        "error": snapshot.error,
        "timezone": schedule.timezone,
        "schedule_time": f"{schedule.hour:02d}:{schedule.minute:02d}",
        "next_run_at": next_daily_run_utc(
            current_time,
            schedule.timezone,
            schedule.hour,
            schedule.minute,
        ),
    }


def _schedule_state(schedule, last_scheduled_run, current_time, clock_offset_seconds):
    if not schedule.enabled:
        return "disabled", None

    timezone = ZoneInfo(schedule.timezone)
    local_now = current_time.astimezone(timezone)
    scheduled_today = local_now.replace(
        hour=schedule.hour,
        minute=schedule.minute,
        second=0,
        microsecond=0,
    )
    if last_scheduled_run is not None:
        corrected_started_at = last_scheduled_run.started_at + timedelta(
            seconds=clock_offset_seconds
        )
        if corrected_started_at.astimezone(timezone).date() == local_now.date():
            status = last_scheduled_run.status
            status_value = status.value if isinstance(status, RunStatus) else str(status)
            return status_value, scheduled_today.astimezone(UTC)

    if local_now < scheduled_today:
        return "pending", scheduled_today.astimezone(UTC)
    if local_now < scheduled_today + timedelta(minutes=5):
        return "starting", scheduled_today.astimezone(UTC)
    return "overdue", scheduled_today.astimezone(UTC)


@router.get("/time")
async def read_network_time(session: AsyncSession = Depends(get_session)):
    await network_clock.sync()
    schedule, _ = await ensure_default_settings(session)
    return _time_status(schedule)


@router.post("/time/actions/sync")
async def synchronize_network_time(session: AsyncSession = Depends(get_session)):
    await network_clock.sync(force=True)
    schedule, _ = await ensure_default_settings(session)
    return _time_status(schedule)


@router.get("/status")
async def system_status(session: AsyncSession = Depends(get_session)):
    await network_clock.sync()
    schedule, email = await ensure_default_settings(session)
    topics = list(await session.scalars(select(Topic).where(Topic.enabled.is_(True))))
    enabled_profile_ids = set(
        await session.scalars(select(LLMProfile.id).where(LLMProfile.enabled.is_(True)))
    )
    default_profile_id = await get_default_profile_id(session)
    topics_without_model = sum(
        1
        for topic in topics
        if (
            topic.llm_profile_id not in enabled_profile_ids
            if topic.llm_profile_id is not None
            else default_profile_id is None
        )
    )
    last_run = await session.scalar(select(RunLog).order_by(RunLog.started_at.desc()).limit(1))
    last_scheduled_run = await session.scalar(
        select(RunLog)
        .where(RunLog.trigger.like("scheduled%"))
        .order_by(RunLog.started_at.desc())
        .limit(1)
    )
    current_time = network_clock.utcnow()
    clock_snapshot = network_clock.snapshot()
    schedule_state, scheduled_today_at = _schedule_state(
        schedule,
        last_scheduled_run,
        current_time,
        clock_snapshot.offset_seconds,
    )
    return {
        "ready": bool(topics) and topics_without_model == 0,
        "enabled_topics": len(topics),
        "enabled_profiles": len(enabled_profile_ids),
        "topics_without_model": topics_without_model,
        "schedule_enabled": schedule.enabled,
        "schedule_state": schedule_state,
        "schedule_time": f"{schedule.hour:02d}:{schedule.minute:02d}",
        "schedule_timezone": schedule.timezone,
        "scheduled_today_at": scheduled_today_at,
        "current_time": current_time,
        "email_enabled": email.enabled,
        "last_run": RunLogOut.model_validate(last_run) if last_run else None,
    }
