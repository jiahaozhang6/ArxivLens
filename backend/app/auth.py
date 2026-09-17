import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

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
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAX_MEMORY = 64 * 1024 * 1024
PASSWORD_MIN_LENGTH = 14
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_GUEST_TOKEN_PREFIX = "guest"


@dataclass(slots=True)
class AuthContext:
    user: AdminUser | None
    session: AuthSession | None
    role: Literal["admin", "guest"]
    expires_at: datetime


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


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(24)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
        maxmem=SCRYPT_MAX_MEMORY,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        parts = encoded.split("$")
        if parts[0] == "scrypt" and len(parts) == 6:
            _, n_text, r_text, p_text, salt_text, digest_text = parts
            n, r, p = int(n_text), int(r_text), int(p_text)
            if n < 2**14 or n > 2**18 or not 1 <= r <= 16 or not 1 <= p <= 8:
                return False
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=_decode(salt_text),
                n=n,
                r=r,
                p=p,
                dklen=32,
                maxmem=256 * 1024 * 1024,
            )
        elif parts[0] == "pbkdf2_sha256" and len(parts) == 4:
            _, iteration_text, salt_text, digest_text = parts
            iterations = int(iteration_text)
            if iterations < 100_000 or iterations > 2_000_000:
                return False
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                _decode(salt_text),
                iterations,
            )
        else:
            return False
        return hmac.compare_digest(actual, _decode(digest_text))
    except (ValueError, TypeError, MemoryError):
        return False


def password_needs_rehash(encoded: str) -> bool:
    return not encoded.startswith(f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$")


def validate_password_strength(password: str, username: str | None = None) -> None:
    if not PASSWORD_MIN_LENGTH <= len(password) <= 128:
        raise ValueError(f"密码长度应为 {PASSWORD_MIN_LENGTH} 到 128 个字符")
    normalized = password.casefold()
    if username and len(username.strip()) >= 3 and username.strip().casefold() in normalized:
        raise ValueError("密码不能包含管理员用户名")
    if normalized in {
        "password123456",
        "12345678901234",
        "qwertyuiop12345",
        "administrator123",
        "admin123456789",
    }:
        raise ValueError("密码过于常见，请使用独立的长密码或密码短语")
    categories = sum(
        (
            any(character.islower() for character in password),
            any(character.isupper() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() and not character.isspace() for character in password),
        )
    )
    if len(password) < 20 and categories < 3:
        raise ValueError("少于 20 个字符的密码需包含大小写字母、数字、符号中的至少三类")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_guest_token() -> tuple[str, datetime]:
    expires_at = utcnow() + timedelta(hours=get_settings().auth_session_hours)
    payload = f"{_GUEST_TOKEN_PREFIX}.{int(expires_at.timestamp())}.{secrets.token_urlsafe(24)}"
    signature = hmac.new(
        get_settings().secret_key.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}", expires_at


def _guest_token_expiration(token: str) -> datetime | None:
    try:
        prefix, expires_text, nonce, signature = token.split(".", 3)
        if prefix != _GUEST_TOKEN_PREFIX or not nonce:
            return None
        payload = f"{prefix}.{expires_text}.{nonce}"
        expected = hmac.new(
            get_settings().secret_key.encode("utf-8"),
            payload.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        expires_at = datetime.fromtimestamp(int(expires_text), UTC)
        return expires_at if expires_at > utcnow() else None
    except (ValueError, TypeError, OverflowError):
        return None


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


def set_guest_cookie(response: Response, token: str) -> None:
    max_age = get_settings().auth_session_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        **_cookie_options(max_age),
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        secure=get_settings().auth_cookie_secure,
        samesite="strict",
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
    guest_expiration = _guest_token_expiration(token)
    if guest_expiration is not None:
        return AuthContext(
            user=None,
            session=None,
            role="guest",
            expires_at=guest_expiration,
        )
    auth_session = await session.scalar(
        select(AuthSession)
        .where(AuthSession.token_hash == _token_hash(token))
        .options(joinedload(AuthSession.user))
    )
    if auth_session is None:
        return None
    current_user_agent = (request.headers.get("user-agent") or "")[:500] or None
    user_agent_changed = bool(
        auth_session.user_agent
        and current_user_agent
        and not hmac.compare_digest(auth_session.user_agent, current_user_agent)
    )
    if auth_session.expires_at <= utcnow() or not auth_session.user.is_active or user_agent_changed:
        await session.delete(auth_session)
        await session.commit()
        return None
    return AuthContext(
        user=auth_session.user,
        session=auth_session,
        role="admin",
        expires_at=auth_session.expires_at,
    )


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
    if context.session is not None:
        validate_csrf(request, context.session)
    return context


async def require_admin(context: AuthContext = Depends(require_auth_context)) -> AdminUser:
    if context.role != "admin" or context.user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行此操作")
    return context.user


async def require_reader(context: AuthContext = Depends(require_auth_context)) -> AuthContext:
    return context
