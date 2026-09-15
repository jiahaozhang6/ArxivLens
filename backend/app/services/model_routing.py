from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import LLMProfile
from app.services.llm import LLMRequestError

T = TypeVar("T")

_profile_cooldowns: dict[int, tuple[float, str]] = {}
_cooldown_lock = threading.Lock()


def _profile_label(profile: LLMProfile) -> str:
    return f"{profile.name} ({profile.model})"


def _error_text(exc: Exception, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip() or exc.__class__.__name__
    return text[:limit]


def _cooldown_reason(profile_id: int) -> str | None:
    now = time.monotonic()
    with _cooldown_lock:
        entry = _profile_cooldowns.get(profile_id)
        if entry is None:
            return None
        unavailable_until, reason = entry
        if unavailable_until <= now:
            _profile_cooldowns.pop(profile_id, None)
            return None
        remaining = max(int(unavailable_until - now), 1)
        return f"该模型刚刚调用失败，剩余冷却 {remaining} 秒：{reason}"


def _mark_profile_failed(profile_id: int, reason: str) -> None:
    cooldown = max(get_settings().llm_profile_cooldown_seconds, 0)
    if cooldown <= 0:
        return
    with _cooldown_lock:
        _profile_cooldowns[profile_id] = (time.monotonic() + cooldown, reason)


def _mark_profile_healthy(profile_id: int) -> None:
    with _cooldown_lock:
        _profile_cooldowns.pop(profile_id, None)


def reset_model_cooldowns() -> None:
    """Clear process-local health state. Intended for tests and explicit recovery."""
    with _cooldown_lock:
        _profile_cooldowns.clear()


def _profile_info(profile: LLMProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.id,
        "name": profile.name,
        "provider": profile.provider,
        "model": profile.model,
    }


@dataclass(slots=True)
class RoutedModelResult(Generic[T]):
    value: T
    profile: LLMProfile
    preferred_profile_id: int
    attempts: list[dict[str, Any]]

    @property
    def fallback_used(self) -> bool:
        return self.profile.id != self.preferred_profile_id

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "preferred_profile_id": self.preferred_profile_id,
            "used": _profile_info(self.profile),
            "fallback_used": self.fallback_used,
            "attempts": self.attempts,
        }

    @property
    def warning(self) -> str | None:
        if not self.fallback_used:
            return None
        failed = [item for item in self.attempts if item["status"] != "completed"]
        reasons = "；".join(
            f"{item['name']}：{item['error']}" for item in failed
        )
        message = f"首选模型异常，已自动切换至“{_profile_label(self.profile)}”"
        return f"{message}。{reasons}"[:1800]


class AllModelProfilesFailed(LLMRequestError):
    def __init__(self, attempts: list[dict[str, Any]]):
        self.attempts = attempts
        details = "；".join(
            f"{item['name']}：{item['error']}" for item in attempts
        )
        super().__init__(f"所有可用云模型均调用失败。{details}"[:4000])


async def _load_candidates(
    session: AsyncSession,
    preferred_profile_id: int | None,
) -> tuple[list[LLMProfile], int, list[dict[str, Any]]]:
    profiles = list(
        await session.scalars(
            select(LLMProfile)
            .where(LLMProfile.enabled.is_(True))
            .order_by(LLMProfile.is_default.desc(), LLMProfile.name, LLMProfile.id)
        )
    )
    initial_attempts: list[dict[str, Any]] = []
    preferred = None
    if preferred_profile_id is not None:
        preferred = next(
            (profile for profile in profiles if profile.id == preferred_profile_id),
            None,
        )
        if preferred is None:
            configured = await session.get(LLMProfile, preferred_profile_id)
            reason = "配置不存在" if configured is None else "配置已停用"
            initial_attempts.append(
                {
                    "profile_id": preferred_profile_id,
                    "name": configured.name if configured else f"配置 #{preferred_profile_id}",
                    "provider": configured.provider if configured else "unknown",
                    "model": configured.model if configured else "unknown",
                    "status": "skipped",
                    "error": reason,
                }
            )
    if preferred is not None:
        profiles = [preferred, *(profile for profile in profiles if profile.id != preferred.id)]
    if not profiles:
        raise AllModelProfilesFailed(
            initial_attempts
            or [
                {
                    "profile_id": None,
                    "name": "云模型配置",
                    "provider": "unknown",
                    "model": "unknown",
                    "status": "skipped",
                    "error": "没有已启用的云模型",
                }
            ]
        )
    effective_preferred_id = preferred_profile_id or profiles[0].id
    limit = max(get_settings().llm_fallback_max_profiles, 1)
    return profiles[:limit], effective_preferred_id, initial_attempts


async def run_with_profile_fallback(
    session: AsyncSession,
    preferred_profile_id: int | None,
    operation: Callable[[LLMProfile], Awaitable[T]],
    *,
    honor_cooldown: bool = True,
) -> RoutedModelResult[T]:
    profiles, effective_preferred_id, attempts = await _load_candidates(
        session,
        preferred_profile_id,
    )
    for profile in profiles:
        if honor_cooldown and (reason := _cooldown_reason(profile.id)):
            attempts.append(
                {
                    **_profile_info(profile),
                    "status": "skipped",
                    "error": reason,
                }
            )
            continue
        try:
            timeout = get_settings().llm_profile_timeout_seconds
            async with asyncio.timeout(timeout if timeout > 0 else None):
                value = await operation(profile)
        except TimeoutError:
            reason = f"单模型调用超过 {timeout:g} 秒，已切换备用模型"
            _mark_profile_failed(profile.id, reason)
            attempts.append(
                {
                    **_profile_info(profile),
                    "status": "failed",
                    "error": reason,
                }
            )
            continue
        except (LLMRequestError, ValueError) as exc:
            reason = _error_text(exc)
            _mark_profile_failed(profile.id, reason)
            attempts.append(
                {
                    **_profile_info(profile),
                    "status": "failed",
                    "error": reason,
                }
            )
            continue
        _mark_profile_healthy(profile.id)
        attempts.append(
            {
                **_profile_info(profile),
                "status": "completed",
                "error": "",
            }
        )
        return RoutedModelResult(
            value=value,
            profile=profile,
            preferred_profile_id=effective_preferred_id,
            attempts=attempts,
        )
    raise AllModelProfilesFailed(attempts)


async def stream_with_profile_fallback(
    session: AsyncSession,
    preferred_profile_id: int | None,
    operation: Callable[[LLMProfile], AsyncIterator[str]],
) -> AsyncIterator[dict[str, Any]]:
    """Stream text while retaining the batch analyzer's health and fallback policy."""
    profiles, effective_preferred_id, attempts = await _load_candidates(
        session,
        preferred_profile_id,
    )
    timeout = get_settings().llm_profile_timeout_seconds
    for profile in profiles:
        if reason := _cooldown_reason(profile.id):
            attempts.append({**_profile_info(profile), "status": "skipped", "error": reason})
            continue

        iterator = operation(profile)
        emitted = False
        started = time.monotonic()
        try:
            while True:
                remaining = timeout - (time.monotonic() - started) if timeout > 0 else None
                if remaining is not None and remaining <= 0:
                    raise TimeoutError
                try:
                    chunk = await asyncio.wait_for(anext(iterator), timeout=remaining)
                except StopAsyncIteration:
                    break
                if not chunk:
                    continue
                if not emitted:
                    yield {
                        "type": "model",
                        "profile": _profile_info(profile),
                        "fallback_used": profile.id != effective_preferred_id,
                    }
                emitted = True
                yield {"type": "delta", "text": chunk}
            if not emitted:
                raise LLMRequestError("Cloud model returned no streamed text")
        except asyncio.CancelledError:
            await iterator.aclose()
            raise
        except TimeoutError:
            await iterator.aclose()
            reason = f"单模型调用超过 {timeout:g} 秒，已切换备用模型"
            _mark_profile_failed(profile.id, reason)
            attempts.append({**_profile_info(profile), "status": "failed", "error": reason})
            if emitted:
                yield {"type": "reset", "reason": reason}
            continue
        except (LLMRequestError, ValueError) as exc:
            await iterator.aclose()
            reason = _error_text(exc)
            _mark_profile_failed(profile.id, reason)
            attempts.append({**_profile_info(profile), "status": "failed", "error": reason})
            if emitted:
                yield {"type": "reset", "reason": reason}
            continue

        _mark_profile_healthy(profile.id)
        attempts.append({**_profile_info(profile), "status": "completed", "error": ""})
        routing = RoutedModelResult(
            value=None,
            profile=profile,
            preferred_profile_id=effective_preferred_id,
            attempts=attempts,
        )
        yield {"type": "done", "routing": routing.metadata, "warning": routing.warning}
        return
    raise AllModelProfilesFailed(attempts)
