import asyncio

from sqlalchemy import select

from app.database import SessionLocal, close_db, init_db
from app.models import EmailSettings, LLMProfile
from app.security import decrypt_secret, encrypt_secret


async def migrate_stored_secrets() -> tuple[int, int]:
    llm_updated = 0
    smtp_updated = 0
    async with SessionLocal() as session:
        profiles = list(await session.scalars(select(LLMProfile)))
        for profile in profiles:
            if not profile.encrypted_api_key:
                continue
            plaintext = decrypt_secret(profile.encrypted_api_key, "llm")
            profile.encrypted_api_key = encrypt_secret(plaintext, "llm")
            llm_updated += 1

        email_configs = list(await session.scalars(select(EmailSettings)))
        for email in email_configs:
            if not email.encrypted_password:
                continue
            plaintext = decrypt_secret(email.encrypted_password, "smtp")
            email.encrypted_password = encrypt_secret(plaintext, "smtp")
            smtp_updated += 1

        await session.commit()
    return llm_updated, smtp_updated


async def _main() -> None:
    await init_db()
    try:
        llm_updated, smtp_updated = await migrate_stored_secrets()
    finally:
        await close_db()
    print(f"Re-encrypted {llm_updated} LLM secret(s) and {smtp_updated} SMTP secret(s).")


if __name__ == "__main__":
    asyncio.run(_main())
