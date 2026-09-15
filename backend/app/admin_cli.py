import argparse
import asyncio
import getpass

from sqlalchemy import delete, select

from app.auth import hash_password, normalize_username
from app.database import SessionLocal, close_db, init_db
from app.models import AdminUser, AuthSession, utcnow


async def _find_user(username: str | None) -> AdminUser | None:
    async with SessionLocal() as session:
        if username:
            normalized = normalize_username(username)
            return await session.scalar(
                select(AdminUser).where(AdminUser.username_normalized == normalized)
            )
        return await session.scalar(select(AdminUser).order_by(AdminUser.id).limit(1))


async def _reset_password(username: str | None, password: str) -> str:
    async with SessionLocal() as session:
        if username:
            normalized = normalize_username(username)
            user = await session.scalar(
                select(AdminUser).where(AdminUser.username_normalized == normalized)
            )
        else:
            user = await session.scalar(select(AdminUser).order_by(AdminUser.id).limit(1))
        if user is None:
            raise RuntimeError("No administrator account exists. Open the web login page first.")
        user.password_hash = hash_password(password)
        user.password_changed_at = utcnow()
        user.failed_login_attempts = 0
        user.locked_until = None
        await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        await session.commit()
        return user.username


async def _unlock(username: str | None) -> str:
    async with SessionLocal() as session:
        if username:
            normalized = normalize_username(username)
            user = await session.scalar(
                select(AdminUser).where(AdminUser.username_normalized == normalized)
            )
        else:
            user = await session.scalar(select(AdminUser).order_by(AdminUser.id).limit(1))
        if user is None:
            raise RuntimeError("No administrator account exists.")
        user.failed_login_attempts = 0
        user.locked_until = None
        await session.commit()
        return user.username


def _read_new_password() -> str:
    password = getpass.getpass("New password (at least 12 characters): ")
    confirmation = getpass.getpass("Confirm new password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12 to 128 characters")
    return password


async def _run(args) -> str:
    await init_db()
    try:
        if args.command == "reset-password":
            return await _reset_password(args.username, _read_new_password())
        if args.command == "unlock":
            return await _unlock(args.username)
        raise RuntimeError("Unknown command")
    finally:
        await close_db()


def main() -> None:
    parser = argparse.ArgumentParser(description="ArxivLens local administrator recovery")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("reset-password", "Reset the administrator password and revoke all sessions"),
        ("unlock", "Clear a login lockout"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("--username", help="Administrator username; optional")
    args = parser.parse_args()
    try:
        username = asyncio.run(_run(args))
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Administrator updated: {username}")


if __name__ == "__main__":
    main()
