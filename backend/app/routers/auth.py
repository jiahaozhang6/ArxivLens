from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    AuthContext,
    clear_auth_cookies,
    create_auth_session,
    create_guest_token,
    get_auth_context,
    hash_password,
    normalize_username,
    password_needs_rehash,
    require_admin,
    require_auth_context,
    set_auth_cookies,
    set_csrf_cookie,
    set_guest_cookie,
    validate_password_strength,
    verify_password,
)
from app.config import get_settings
from app.database import get_session
from app.models import AdminUser, AuthSession, utcnow
from app.schemas import AdminUserOut, AuthCredentials, AuthStatusOut, PasswordChangeRequest

router = APIRouter(prefix="/api/auth", tags=["authentication"])
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password-value")


def _status_payload(user: AdminUser, auth_session: AuthSession) -> AuthStatusOut:
    return AuthStatusOut(
        setup_required=False,
        authenticated=True,
        role="admin",
        user=AdminUserOut.model_validate(user),
        session_expires_at=auth_session.expires_at,
    )


@router.get("/status", response_model=AuthStatusOut)
async def read_auth_status(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    context = await get_auth_context(request, session)
    if context is not None:
        if context.role == "guest":
            return AuthStatusOut(
                setup_required=False,
                authenticated=True,
                role="guest",
                session_expires_at=context.expires_at,
            )
        if context.user is not None and context.session is not None:
            set_csrf_cookie(response, context.session.csrf_token, context.session.expires_at)
            return _status_payload(context.user, context.session)
    user_count = await session.scalar(select(func.count()).select_from(AdminUser)) or 0
    return AuthStatusOut(setup_required=user_count == 0, authenticated=False)


@router.post("/setup", response_model=AuthStatusOut, status_code=status.HTTP_201_CREATED)
async def setup_admin(
    payload: AuthCredentials,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    if await session.scalar(select(func.count()).select_from(AdminUser)):
        raise HTTPException(status_code=409, detail="管理员账号已经创建")
    try:
        normalized = normalize_username(payload.username)
        validate_password_strength(payload.password, payload.username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user = AdminUser(
        id=1,
        username=payload.username.strip(),
        username_normalized=normalized,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="管理员账号已经创建") from exc
    await session.refresh(user)
    raw_token, auth_session = await create_auth_session(session, user, request)
    set_auth_cookies(response, raw_token, auth_session.csrf_token)
    return _status_payload(user, auth_session)


@router.post("/guest", response_model=AuthStatusOut)
async def guest_login(response: Response):
    token, expires_at = create_guest_token()
    set_guest_cookie(response, token)
    return AuthStatusOut(
        setup_required=False,
        authenticated=True,
        role="guest",
        session_expires_at=expires_at,
    )


@router.post("/login", response_model=AuthStatusOut)
async def login(
    payload: AuthCredentials,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    try:
        normalized = normalize_username(payload.username)
    except ValueError:
        normalized = payload.username.strip().casefold()
    user = await session.scalar(select(AdminUser).where(AdminUser.username_normalized == normalized))
    if user is None:
        verify_password(payload.password, _DUMMY_PASSWORD_HASH)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    now = utcnow()
    if user.locked_until and user.locked_until > now:
        retry_after = max(int((user.locked_until - now).total_seconds()), 1)
        raise HTTPException(
            status_code=429,
            detail="登录尝试过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    if not verify_password(payload.password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= get_settings().auth_login_max_attempts:
            user.locked_until = now + timedelta(minutes=get_settings().auth_lock_minutes)
        await session.commit()
        if user.locked_until:
            raise HTTPException(status_code=429, detail="登录尝试过多，请稍后再试")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    raw_token, auth_session = await create_auth_session(session, user, request)
    set_auth_cookies(response, raw_token, auth_session.csrf_token)
    return _status_payload(user, auth_session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    context: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_session),
):
    if context.session is not None:
        await session.delete(context.session)
        await session.commit()
    clear_auth_cookies(response)


@router.post("/change-password", response_model=AuthStatusOut)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: AdminUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    try:
        validate_password_strength(payload.new_password, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    await session.commit()
    raw_token, auth_session = await create_auth_session(session, user, request)
    set_auth_cookies(response, raw_token, auth_session.csrf_token)
    return _status_payload(user, auth_session)
