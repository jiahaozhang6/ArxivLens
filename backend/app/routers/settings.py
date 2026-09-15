from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_session
from app.models import EmailSettings
from app.schemas import (
    EmailSettingsOut,
    EmailSettingsUpdate,
    ScheduleSettingsOut,
    ScheduleSettingsUpdate,
)
from app.security import encrypt_secret
from app.services.email_service import send_test_email, send_today_digest
from app.services.settings_service import get_email_settings, get_schedule_settings

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(require_admin)],
)


def _email_out(email) -> EmailSettingsOut:
    return EmailSettingsOut(
        enabled=email.enabled,
        smtp_host=email.smtp_host,
        smtp_port=email.smtp_port,
        username=email.username,
        from_email=email.from_email,
        from_name=email.from_name,
        recipients=email.recipients,
        security=email.security,
        subject_prefix=email.subject_prefix,
        has_password=bool(email.encrypted_password),
        updated_at=email.updated_at,
    )


@router.get("")
async def read_settings(session: AsyncSession = Depends(get_session)):
    schedule = await get_schedule_settings(session)
    email = await get_email_settings(session)
    return {
        "schedule": ScheduleSettingsOut(
            enabled=schedule.enabled,
            timezone=schedule.timezone,
            hour=schedule.hour,
            minute=schedule.minute,
            digest_language=schedule.digest_language,
            public_base_url=schedule.public_base_url,
            updated_at=schedule.updated_at,
        ),
        "email": _email_out(email),
    }


@router.put("/schedule", response_model=ScheduleSettingsOut)
async def update_schedule(
    payload: ScheduleSettingsUpdate,
    session: AsyncSession = Depends(get_session),
):
    try:
        ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail="Unknown IANA timezone") from exc
    schedule = await get_schedule_settings(session)
    for key, value in payload.model_dump().items():
        setattr(schedule, key, value)
    await session.commit()
    await session.refresh(schedule)
    return ScheduleSettingsOut(
        **payload.model_dump(),
        updated_at=schedule.updated_at,
    )


@router.put("/email", response_model=EmailSettingsOut)
async def update_email(
    payload: EmailSettingsUpdate,
    session: AsyncSession = Depends(get_session),
):
    if payload.enabled and (
        not payload.smtp_host or not payload.from_email or not payload.recipients
    ):
        raise HTTPException(
            status_code=400,
            detail="Enabled email delivery requires SMTP host, sender, and recipients",
        )
    email = await get_email_settings(session)
    values = payload.model_dump(exclude={"password"})
    values["recipients"] = [str(item) for item in payload.recipients]
    for key, value in values.items():
        setattr(email, key, value)
    if payload.password is not None:
        email.encrypted_password = encrypt_secret(payload.password)
    await session.commit()
    await session.refresh(email)
    return _email_out(email)


@router.post("/email/actions/test")
async def test_email(
    payload: EmailSettingsUpdate | None = None,
    session: AsyncSession = Depends(get_session),
):
    try:
        saved_email = await get_email_settings(session)
        if payload is None:
            test_config = saved_email
        else:
            test_config = EmailSettings(
                enabled=payload.enabled,
                smtp_host=payload.smtp_host,
                smtp_port=payload.smtp_port,
                username=payload.username,
                from_email=payload.from_email,
                from_name=payload.from_name,
                recipients=[str(item) for item in payload.recipients],
                security=payload.security,
                subject_prefix=payload.subject_prefix,
                encrypted_password=(
                    encrypt_secret(payload.password)
                    if payload.password is not None
                    else saved_email.encrypted_password
                ),
            )
        await send_test_email(test_config)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/email/actions/send-today")
async def send_today_email():
    try:
        return await send_today_digest()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
