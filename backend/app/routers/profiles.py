from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_session
from app.models import LLMProfile
from app.schemas import (
    LLMProfileCreate,
    LLMProfileOut,
    LLMProfileUpdate,
    ModelDiscoveryOut,
    ModelDiscoveryRequest,
    ProviderPreset,
)
from app.security import decrypt_secret, encrypt_secret
from app.services.llm import list_available_models, test_profile_connection
from app.services.settings_service import PROVIDER_PRESETS

router = APIRouter(
    prefix="/api/llm-profiles",
    tags=["llm-profiles"],
    dependencies=[Depends(require_admin)],
)


def _serialize(profile: LLMProfile) -> LLMProfileOut:
    return LLMProfileOut(
        **{
            column.name: getattr(profile, column.name)
            for column in profile.__table__.columns
            if column.name != "encrypted_api_key"
        },
        has_api_key=bool(profile.encrypted_api_key),
    )


@router.get("/presets", response_model=list[ProviderPreset])
async def list_presets():
    return PROVIDER_PRESETS


@router.post("/actions/models", response_model=ModelDiscoveryOut)
async def discover_models(
    payload: ModelDiscoveryRequest,
    session: AsyncSession = Depends(get_session),
):
    api_key = (payload.api_key or "").strip()
    if not api_key and payload.profile_id is not None:
        profile = await session.get(LLMProfile, payload.profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="LLM profile not found")
        profile_protocol = getattr(profile.protocol, "value", profile.protocol)
        if profile.base_url.rstrip("/") != payload.base_url.rstrip("/") or (
            profile_protocol != payload.protocol.value
        ):
            raise HTTPException(
                status_code=400,
                detail="Re-enter the API key after changing the protocol or API Base URL",
            )
        api_key = decrypt_secret(profile.encrypted_api_key) or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="Enter an API key before fetching models")

    try:
        models = await list_available_models(payload.protocol.value, payload.base_url, api_key)
        return {"models": models}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("", response_model=list[LLMProfileOut])
async def list_profiles(session: AsyncSession = Depends(get_session)):
    profiles = await session.scalars(
        select(LLMProfile).order_by(
            LLMProfile.is_default.desc(),
            LLMProfile.name,
            LLMProfile.id,
        )
    )
    return [_serialize(profile) for profile in profiles]


@router.post("", response_model=LLMProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: LLMProfileCreate,
    session: AsyncSession = Depends(get_session),
):
    values = payload.model_dump(exclude={"api_key"})
    profile = LLMProfile(**values, encrypted_api_key=encrypt_secret(payload.api_key))
    if profile.is_default:
        await session.execute(update(LLMProfile).values(is_default=False))
    session.add(profile)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A profile with this name already exists"
        ) from exc
    await session.refresh(profile)
    return _serialize(profile)


@router.put("/{profile_id}", response_model=LLMProfileOut)
async def update_profile(
    profile_id: int,
    payload: LLMProfileUpdate,
    session: AsyncSession = Depends(get_session),
):
    profile = await session.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="LLM profile not found")
    values = payload.model_dump(exclude_unset=True)
    if "api_key" in values:
        profile.encrypted_api_key = encrypt_secret(values.pop("api_key"))
    if values.get("is_default"):
        await session.execute(
            update(LLMProfile).where(LLMProfile.id != profile_id).values(is_default=False)
        )
    for key, value in values.items():
        setattr(profile, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A profile with this name already exists"
        ) from exc
    await session.refresh(profile)
    return _serialize(profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: int, session: AsyncSession = Depends(get_session)):
    profile = await session.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="LLM profile not found")
    await session.delete(profile)
    await session.commit()


@router.post("/{profile_id}/actions/test")
async def test_profile(profile_id: int, session: AsyncSession = Depends(get_session)):
    profile = await session.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="LLM profile not found")
    try:
        result = await test_profile_connection(profile)
        return {"ok": True, "summary": result.summary}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
