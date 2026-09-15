import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import get_settings
from app.database import get_session
from app.models import AdminUser, AuthSession, utcnow

SESSION_COOKIE = "arxivlens_session"
CSRF_COOKIE = "arxivlens_csrf"
PBKDF2_ITERATIONS = 600_000
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(slots=True)
class AuthContext:
    user: AdminUser
    session: AuthSession


def normalize_username(value: str) -> str:
    username = value.strip()
    if not 3 <= len(username) <= 64:
        raise ValueError("用户名长度应为 3 到 64 个字符")
    if any(character.isspace() or ord(character) < 32 for character in username):
        raise ValueError("用户名不能包含空格或控制字符")
    return username.casefold()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iteration_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _decode(salt_text),
            iterations,
        )
        return hmac.compare_digest(actual, _decode(digest_text))
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _cookie_options(max_age: int) -> dict:
    return {
        "path": "/",
        "max_age": max_age,
        "secure": get_settings().auth_cookie_secure,
        "samesite": "strict",
    }


def set_auth_cookies(response: Response, token: str, csrf_token: str) -> None:
    max_age = get_settings().auth_session_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        **_cookie_options(max_age),
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        **_cookie_options(max_age),
    )


def set_csrf_cookie(response: Response, csrf_token: str, expires_at) -> None:
    remaining = max(int((expires_at - utcnow()).total_seconds()), 1)
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        **_cookie_options(remaining),
    )


def clear_auth_cookies(response: Response) -> None:
    options = {
        "path": "/",
        "secure": get_settings().auth_cookie_secure,
        "samesite": "strict",
    }
    response.delete_cookie(SESSION_COOKIE, httponly=True, **options)
    response.delete_cookie(CSRF_COOKIE, httponly=False, **options)


async def create_auth_session(
    session: AsyncSession,
    user: AdminUser,
    request: Request,
) -> tuple[str, AuthSession]:
    now = utcnow()
    await session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    existing = list(
        await session.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user.id)
            .order_by(AuthSession.created_at.desc())
        )
    )
    for stale in existing[9:]:
        await session.delete(stale)

    raw_token = secrets.token_urlsafe(48)
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        csrf_token=secrets.token_urlsafe(36),
        created_ip=(request.client.host[:64] if request.client else None),
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        expires_at=now + timedelta(hours=get_settings().auth_session_hours),
    )
    session.add(auth_session)
    await session.commit()
    await session.refresh(auth_session)
    return raw_token, auth_session


async def get_auth_context(request: Request, session: AsyncSession) -> AuthContext | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    auth_session = await session.scalar(
        select(AuthSession)
        .where(AuthSession.token_hash == _token_hash(token))
        .options(joinedload(AuthSession.user))
    )
    if auth_session is None:
        return None
    if auth_session.expires_at <= utcnow() or not auth_session.user.is_active:
        await session.delete(auth_session)
        await session.commit()
        return None
    return AuthContext(user=auth_session.user, session=auth_session)


def validate_csrf(request: Request, auth_session: AuthSession) -> None:
    if request.method.upper() in _SAFE_METHODS:
        return
    header_token = request.headers.get("x-csrf-token")
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if (
        not header_token
        or not cookie_token
        or not hmac.compare_digest(header_token, cookie_token)
        or not hmac.compare_digest(header_token, auth_session.csrf_token)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="安全校验失败，请刷新页面后重试")


async def require_auth_context(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    context = await get_auth_context(request, session)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Session"},
        )
    validate_csrf(request, context.session)
    return context


async def require_admin(context: AuthContext = Depends(require_auth_context)) -> AdminUser:
    return context.user
