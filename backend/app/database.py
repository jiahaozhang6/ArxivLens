import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from filelock import FileLock
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from alembic import command
from app.config import PROJECT_ROOT, get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

if settings.database_url.startswith("sqlite"):
    db_file = settings.database_url.rsplit("///", 1)[-1]
    if db_file and db_file != ":memory:":
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"timeout": 30} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def _upgrade_schema_sync() -> None:
    lock_path = PROJECT_ROOT / "data" / ".schema-migration.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    alembic_config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    with FileLock(str(lock_path), timeout=180):
        command.upgrade(alembic_config, "head")


async def init_db() -> None:
    await asyncio.to_thread(_upgrade_schema_sync)


async def close_db() -> None:
    await engine.dispose()
