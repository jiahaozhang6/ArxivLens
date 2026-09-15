from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailSettings, ScheduleSettings
from app.schemas import ProviderPreset

PROVIDER_PRESETS = [
    ProviderPreset(
        id="deepseek",
        label="DeepSeek",
        region="CN",
        protocol="openai_compatible",
        base_url="https://api.deepseek.com",
        model_hint="deepseek-v4-flash",
        api_key_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderPreset(
        id="qwen",
        label="Alibaba Cloud Qwen",
        region="CN",
        protocol="openai_compatible",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_hint="qwen-plus",
        api_key_url="https://bailian.console.aliyun.com/",
    ),
    ProviderPreset(
        id="zhipu",
        label="Zhipu GLM",
        region="CN",
        protocol="openai_compatible",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model_hint="glm-5.3",
        api_key_url="https://open.bigmodel.cn/usercenter/apikeys",
    ),
    ProviderPreset(
        id="moonshot",
        label="Moonshot Kimi",
        region="CN",
        protocol="openai_compatible",
        base_url="https://api.moonshot.ai/v1",
        model_hint="kimi-k3",
        api_key_url="https://platform.kimi.ai/console/api-keys",
    ),
    ProviderPreset(
        id="siliconflow",
        label="SiliconFlow",
        region="CN",
        protocol="openai_compatible",
        base_url="https://api.siliconflow.cn/v1",
        model_hint="deepseek-ai/DeepSeek-V3.2",
        api_key_url="https://cloud.siliconflow.cn/account/ak",
    ),
    ProviderPreset(
        id="openai",
        label="OpenAI",
        region="Global",
        protocol="openai_compatible",
        base_url="https://api.openai.com/v1",
        model_hint="gpt-5.4-mini",
        api_key_url="https://platform.openai.com/api-keys",
    ),
    ProviderPreset(
        id="anthropic",
        label="Anthropic Claude",
        region="Global",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        model_hint="claude-sonnet-5",
        api_key_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderPreset(
        id="gemini",
        label="Google Gemini",
        region="Global",
        protocol="openai_compatible",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model_hint="gemini-3.8-flash",
        api_key_url="https://aistudio.google.com/app/apikey",
    ),
    ProviderPreset(
        id="custom",
        label="Custom cloud API",
        region="Custom",
        protocol="openai_compatible",
        base_url="https://api.example.com/v1",
        model_hint="provider-model-id",
        api_key_url="",
    ),
]


async def ensure_default_settings(session: AsyncSession) -> tuple[ScheduleSettings, EmailSettings]:
    schedule = await session.get(ScheduleSettings, 1)
    email = await session.get(EmailSettings, 1)
    changed = False
    if schedule is None:
        schedule = ScheduleSettings(id=1)
        session.add(schedule)
        changed = True
    if email is None:
        email = EmailSettings(id=1)
        session.add(email)
        changed = True
    if changed:
        await session.commit()
        await session.refresh(schedule)
        await session.refresh(email)
    return schedule, email


async def get_schedule_settings(session: AsyncSession) -> ScheduleSettings:
    schedule, _ = await ensure_default_settings(session)
    return schedule


async def get_email_settings(session: AsyncSession) -> EmailSettings:
    _, email = await ensure_default_settings(session)
    return email


async def get_default_profile_id(session: AsyncSession) -> int | None:
    from app.models import LLMProfile

    return await session.scalar(
        select(LLMProfile.id)
        .where(LLMProfile.enabled.is_(True), LLMProfile.is_default.is_(True))
        .order_by(LLMProfile.id)
        .limit(1)
    )
