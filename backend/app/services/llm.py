import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.models import LLMProfile, Paper, Topic
from app.security import decrypt_secret


class LLMRequestError(RuntimeError):
    pass


class AnalysisPayload(BaseModel):
    summary: str
    research_question: str
    contributions: list[str] = Field(default_factory=list)
    methodology: str
    experiments: str
    limitations: list[str] = Field(default_factory=list)
    reading_advice: str
    relevance_reason: str
    keywords: list[str] = Field(default_factory=list)
    novelty_score: float = Field(ge=0, le=10)
    rigor_score: float = Field(ge=0, le=10)
    relevance_score: float = Field(ge=0, le=10)

    @field_validator(
        "summary",
        "research_question",
        "methodology",
        "experiments",
        "reading_advice",
        "relevance_reason",
        mode="before",
    )
    @classmethod
    def normalize_prose(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return "\n".join(
                f"- {str(item).strip()}" for item in value if str(item).strip()
            )
        return value

    @field_validator("contributions", "limitations", "keywords", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip(" -") for item in value.split("\n") if item.strip(" -")]
        return [str(item).strip() for item in value if str(item).strip()]


class TopicQueryPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    keywords: list[str] = Field(min_length=1, max_length=12)
    relevance_prompt: str = Field(min_length=1, max_length=2000)

    @field_validator("keywords", mode="before")
    @classmethod
    def normalize_keywords(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = re.split(r"[,;\n]", value)
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            keyword = re.sub(r"\s+", " ", str(item)).strip(' \t\r\n"')
            normalized = keyword.casefold()
            if keyword and normalized not in seen:
                seen.add(normalized)
                result.append(keyword[:120])
        return result[:12]


SYSTEM_PROMPT = """You are a rigorous research assistant. Analyze only the supplied paper text.
Do not invent experimental results, datasets, metrics, citations, or claims. If evidence is missing,
state that clearly. Return one valid JSON object without Markdown fences. The writing language must
match the requested language. Scores are numbers from 0 to 10.

Required JSON keys:
summary, research_question, contributions, methodology, experiments, limitations, reading_advice,
relevance_reason, keywords, novelty_score, rigor_score, relevance_score.
"""


TOPIC_QUERY_SYSTEM_PROMPT = """You design precise arXiv alerts for researchers. Convert the user's
research interest into a compact search plan. Return one valid JSON object without Markdown fences.

Required JSON keys:
- name: a concise Chinese subscription name, at most 24 Chinese characters
- keywords: 3 to 8 English phrases likely to appear in an arXiv title or abstract; include important
  established acronyms as separate phrases, avoid generic words such as model, method, or AI alone
- relevance_prompt: concise Chinese criteria that help another model judge whether a paper is truly
  relevant, including the main inclusion focus and obvious exclusions

Do not include arXiv query syntax, category codes, explanations, or duplicate keyword variants.
"""


def _build_user_prompt(
    paper: Paper,
    topic: Topic | None,
    source_text: str,
    source_mode: str,
    language: str,
) -> str:
    topic_context = ""
    if topic:
        topic_context = (
            f"Topic name: {topic.name}\n"
            f"Topic description: {topic.description or 'Not provided'}\n"
            f"Research relevance criteria: {topic.relevance_prompt or 'Judge against the topic name and query.'}\n"
        )
    return f"""Output language: {language}
Analysis source: {source_mode}
{topic_context}
Paper title: {paper.title}
Authors: {", ".join(paper.authors)}
Primary category: {paper.primary_category or "Unknown"}
Published: {paper.published_at.isoformat()}

Abstract:
{paper.abstract}

Paper text supplied for analysis:
{source_text}

Focus on the actual research question, technical contribution, evidence quality, limitations, and
whether a researcher following the configured topic should read the full paper. Keep each prose field
concise but technically specific. Contributions and limitations must be arrays of short strings.
"""


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^\x60\x60\x60(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\x60\x60\x60$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")
    candidate = cleaned[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # Models commonly place LaTeX such as \epsilon in JSON strings without
        # escaping the backslash. Preserve valid JSON escapes and repair only
        # backslashes that cannot start a JSON escape sequence.
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", candidate)
        try:
            parsed = json.loads(repaired, strict=False)
        except json.JSONDecodeError as repaired_exc:
            raise ValueError(f"LLM response contained invalid JSON: {exc}") from repaired_exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object")
    return parsed


def _merge_extra_body(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    reserved = {"model", "messages", "system", "stream"}
    return {**base, **{key: value for key, value in extra.items() if key not in reserved}}


def _openai_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _anthropic_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1/messages") or base.endswith("/messages"):
        return base
    return f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"


def _openai_models_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base if base.endswith("/models") else f"{base}/models"


def _anthropic_models_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1/messages"):
        base = base[: -len("/messages")]
    elif base.endswith("/messages"):
        base = base[: -len("/messages")]
    if base.endswith("/models"):
        return base
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


def _parse_model_options(data: Any) -> list[dict[str, str]]:
    candidates: Any = data
    if isinstance(data, dict):
        candidates = data.get("data") or data.get("models") or data.get("result") or []
        if isinstance(candidates, dict):
            candidates = (
                candidates.get("data")
                or candidates.get("models")
                or candidates.get("items")
                or []
            )
    if not isinstance(candidates, list):
        return []

    options: dict[str, str] = {}
    for item in candidates:
        if isinstance(item, str):
            model_id = item.strip()
            label = model_id
        elif isinstance(item, dict):
            raw_id = item.get("id") or item.get("model") or item.get("name")
            model_id = str(raw_id).strip() if raw_id is not None else ""
            raw_label = item.get("display_name") or item.get("label") or model_id
            label = str(raw_label).strip() or model_id
        else:
            continue
        if model_id:
            options.setdefault(model_id, label)

    return [
        {"id": model_id, "label": options[model_id]}
        for model_id in sorted(options, key=str.casefold)
    ]


async def list_available_models(
    protocol: str,
    base_url: str,
    api_key: str,
) -> list[dict[str, str]]:
    settings = get_settings()
    if protocol == "anthropic":
        url = _anthropic_models_endpoint(base_url)
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        }
    else:
        url = _openai_models_endpoint(base_url)
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMRequestError(f"Network error while fetching models: {exc}") from exc

    if not response.is_success:
        body = response.text[:1000]
        raise LLMRequestError(f"Model list request failed with HTTP {response.status_code}: {body}")
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMRequestError("Cloud provider returned an invalid JSON model list") from exc

    models = _parse_model_options(data)
    if not models:
        raise LLMRequestError(
            "Cloud provider returned no recognizable models; enter the model ID manually"
        )
    return models[:500]


async def _post_with_retries(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    allow_json_fallback: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    working_payload = dict(payload)
    last_error = "Unknown LLM error"
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        for attempt in range(4):
            try:
                response = await client.post(url, headers=headers, json=working_payload)
            except httpx.HTTPError as exc:
                last_error = f"Network error: {exc}"
                if attempt < 3:
                    await asyncio.sleep(min(2**attempt, 30))
                continue
            if response.is_success:
                try:
                    data = response.json()
                except ValueError:
                    last_error = "Cloud model returned an invalid JSON HTTP response"
                else:
                    if isinstance(data, dict):
                        return data
                    last_error = "Cloud model returned an unexpected HTTP response shape"
                if attempt < 3:
                    await asyncio.sleep(min(2**attempt, 30))
                continue
            body = response.text[:1000]
            last_error = f"HTTP {response.status_code}: {body}"
            if response.status_code == 400 and allow_json_fallback:
                if "response_format" in working_payload:
                    working_payload.pop("response_format", None)
                    continue
                if "temperature" in working_payload:
                    working_payload.pop("temperature", None)
                    continue
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else 2**attempt
                except ValueError:
                    delay = 2**attempt
                await asyncio.sleep(min(max(delay, 1), 30))
                continue
            break
    raise LLMRequestError(last_error)


async def _openai_compatible_completion(
    profile: LLMProfile,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    is_openai_reasoning_model = profile.provider == "openai" and profile.model.startswith(
        ("gpt-5", "o1", "o3", "o4")
    )
    omit_temperature = is_openai_reasoning_model or profile.provider == "moonshot"
    payload: dict[str, Any] = {
        "model": profile.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    if is_openai_reasoning_model:
        payload["max_completion_tokens"] = profile.max_tokens
    else:
        payload["max_tokens"] = profile.max_tokens
    if not omit_temperature:
        payload["temperature"] = profile.temperature
    if profile.supports_json_mode:
        payload["response_format"] = {"type": "json_object"}
    payload = _merge_extra_body(payload, profile.extra_body or {})
    data = await _post_with_retries(
        _openai_endpoint(profile.base_url),
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        payload,
        allow_json_fallback=True,
    )
    choices = data.get("choices") or []
    if not choices:
        raise LLMRequestError("Cloud model returned no choices")
    content = choices[0].get("message", {}).get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    if not content:
        raise LLMRequestError("Cloud model returned an empty response")
    return str(content)


async def _anthropic_completion(
    profile: LLMProfile,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    payload = _merge_extra_body(
        {
            "model": profile.model,
            "max_tokens": profile.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        profile.extra_body or {},
    )
    data = await _post_with_retries(
        _anthropic_endpoint(profile.base_url),
        {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        payload,
    )
    content = data.get("content") or []
    text = "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if not text:
        raise LLMRequestError("Anthropic returned an empty response")
    return text


async def complete_with_profile(
    profile: LLMProfile,
    system_prompt: str,
    user_prompt: str,
) -> str:
    api_key = decrypt_secret(profile.encrypted_api_key)
    if not api_key:
        raise LLMRequestError("This cloud model profile has no API key")
    protocol = getattr(profile.protocol, "value", profile.protocol)
    if protocol == "anthropic":
        return await _anthropic_completion(profile, api_key, system_prompt, user_prompt)
    return await _openai_compatible_completion(profile, api_key, system_prompt, user_prompt)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in value
        )
    return ""


async def _openai_compatible_stream(
    profile: LLMProfile,
    api_key: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    max_tokens: int,
) -> AsyncIterator[str]:
    is_reasoning_model = profile.provider == "openai" and profile.model.startswith(
        ("gpt-5", "o1", "o3", "o4")
    )
    payload: dict[str, Any] = {
        "model": profile.model,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "stream": True,
    }
    if is_reasoning_model:
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["max_tokens"] = max_tokens
    if not is_reasoning_model and profile.provider != "moonshot":
        payload["temperature"] = profile.temperature
    payload = _merge_extra_body(payload, profile.extra_body or {})

    settings = get_settings()
    yielded = False
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        async with client.stream(
            "POST",
            _openai_endpoint(profile.base_url),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        ) as response:
            if not response.is_success:
                body = (await response.aread()).decode(errors="replace")[:1000]
                raise LLMRequestError(f"HTTP {response.status_code}: {body}")
            if "text/event-stream" not in response.headers.get("content-type", ""):
                body = await response.aread()
                try:
                    data = json.loads(body)
                except ValueError as exc:
                    raise LLMRequestError("Cloud model returned an invalid streaming response") from exc
                choices = data.get("choices") or []
                text = _content_text(choices[0].get("message", {}).get("content")) if choices else ""
                if not text:
                    raise LLMRequestError("Cloud model returned an empty response")
                yield text
                return
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                if not payload_text or payload_text == "[DONE]":
                    continue
                try:
                    event = json.loads(payload_text)
                except ValueError:
                    continue
                if event.get("error"):
                    raise LLMRequestError(str(event["error"])[:1000])
                choices = event.get("choices") or []
                if not choices:
                    continue
                text = _content_text(choices[0].get("delta", {}).get("content"))
                if text:
                    yielded = True
                    yield text
    if not yielded:
        raise LLMRequestError("Cloud model returned an empty streamed response")


async def _anthropic_stream(
    profile: LLMProfile,
    api_key: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    max_tokens: int,
) -> AsyncIterator[str]:
    payload = _merge_extra_body(
        {
            "model": profile.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": messages,
            "stream": True,
        },
        profile.extra_body or {},
    )
    settings = get_settings()
    yielded = False
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        async with client.stream(
            "POST",
            _anthropic_endpoint(profile.base_url),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
        ) as response:
            if not response.is_success:
                body = (await response.aread()).decode(errors="replace")[:1000]
                raise LLMRequestError(f"HTTP {response.status_code}: {body}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                if not payload_text:
                    continue
                try:
                    event = json.loads(payload_text)
                except ValueError:
                    continue
                if event.get("type") == "error":
                    raise LLMRequestError(str(event.get("error"))[:1000])
                delta = event.get("delta") or {}
                text = delta.get("text") if isinstance(delta, dict) else None
                if text:
                    yielded = True
                    yield str(text)
    if not yielded:
        raise LLMRequestError("Anthropic returned an empty streamed response")


async def stream_chat_with_profile(
    profile: LLMProfile,
    system_prompt: str,
    messages: list[dict[str, str]],
    max_tokens: int = 1600,
) -> AsyncIterator[str]:
    api_key = decrypt_secret(profile.encrypted_api_key)
    if not api_key:
        raise LLMRequestError("This cloud model profile has no API key")
    output_limit = min(max(max_tokens, 128), profile.max_tokens)
    protocol = getattr(profile.protocol, "value", profile.protocol)
    try:
        if protocol == "anthropic":
            async for chunk in _anthropic_stream(
                profile, api_key, system_prompt, messages, output_limit
            ):
                yield chunk
            return
        async for chunk in _openai_compatible_stream(
            profile, api_key, system_prompt, messages, output_limit
        ):
            yield chunk
    except httpx.HTTPError as exc:
        raise LLMRequestError(f"Network error while streaming model output: {exc}") from exc


def build_arxiv_query(categories: list[str], keywords: list[str]) -> str:
    clean_categories = list(dict.fromkeys(category.strip() for category in categories if category))
    clean_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        cleaned = re.sub(r"\s+", " ", keyword.replace("\\", " ").replace('"', " ")).strip()
        normalized = cleaned.casefold()
        if cleaned and normalized not in seen:
            seen.add(normalized)
            clean_keywords.append(cleaned[:120])

    if not clean_keywords:
        raise ValueError("At least one usable English keyword is required")

    keyword_clause = " OR ".join(f'all:"{keyword}"' for keyword in clean_keywords[:12])
    if not clean_categories:
        return f"({keyword_clause})"
    category_clause = " OR ".join(f"cat:{category}" for category in clean_categories)
    return f"({category_clause}) AND ({keyword_clause})"


async def suggest_topic_query(
    profile: LLMProfile,
    research_focus: str,
    categories: list[str],
) -> dict[str, Any]:
    category_text = ", ".join(categories) if categories else "all arXiv categories"
    user_prompt = f"""User research interest:
{research_focus.strip()}

Selected arXiv category scope: {category_text}

Generate a focused recurring alert. Keywords must be English even when the interest is written in
Chinese. Prefer canonical technical phrases that balance recall and precision.
"""
    response_text = await complete_with_profile(
        profile,
        TOPIC_QUERY_SYSTEM_PROMPT,
        user_prompt,
    )
    payload = TopicQueryPayload.model_validate(extract_json_object(response_text))
    return {
        "name": payload.name.strip()[:120],
        "query": build_arxiv_query(categories, payload.keywords),
        "relevance_prompt": payload.relevance_prompt.strip(),
        "keywords": payload.keywords,
    }


async def analyze_with_profile(
    profile: LLMProfile,
    paper: Paper,
    topic: Topic | None,
    source_text: str,
    source_mode: str,
    language: str,
) -> tuple[AnalysisPayload, dict[str, Any]]:
    user_prompt = _build_user_prompt(paper, topic, source_text, source_mode, language)
    response_text = await complete_with_profile(profile, SYSTEM_PROMPT, user_prompt)
    raw = extract_json_object(response_text)
    return AnalysisPayload.model_validate(raw), raw


async def test_profile_connection(profile: LLMProfile) -> AnalysisPayload:
    now = datetime.now(UTC)
    sample = Paper(
        arxiv_id="test.00001",
        version=1,
        title="Connection test for a research paper analysis service",
        abstract="This synthetic abstract checks structured JSON output without factual claims.",
        authors=["System Test"],
        categories=["cs.AI"],
        primary_category="cs.AI",
        published_at=now,
        updated_at=now,
        abs_url="https://arxiv.org/abs/test.00001",
        pdf_url="https://arxiv.org/pdf/test.00001",
    )
    payload, _ = await analyze_with_profile(
        profile, sample, None, sample.abstract, "abstract", "zh-CN"
    )
    return payload
