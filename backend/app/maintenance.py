import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import PROJECT_ROOT, get_settings


def _sqlite_path() -> Path:
    url = make_url(get_settings().database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        raise RuntimeError("SQLite backup is available only when DATABASE_URL points to a file")
    path = Path(url.database)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def backup_sqlite(output: str | None = None) -> Path:
    source_path = _sqlite_path()
    if not source_path.exists():
        raise FileNotFoundError(f"Database does not exist: {source_path}")

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if output:
        requested = Path(output).expanduser().resolve()
        target_path = requested if requested.suffix else requested / f"arxiv-digest-{timestamp}.db"
    else:
        target_path = PROJECT_ROOT / "backups" / f"arxiv-digest-{timestamp}.db"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path == source_path.resolve():
        raise ValueError("Backup destination must differ from the active database")

    with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as target:
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed: {integrity}")
    try:
        target_path.chmod(0o600)
    except OSError:
        pass
    return target_path


def main() -> None:
    parser = argparse.ArgumentParser(description="ArxivLens maintenance commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup", help="Create a consistent SQLite backup")
    backup_parser.add_argument("--output", help="Destination file or directory")
    args = parser.parse_args()

    if args.command == "backup":
        print(backup_sqlite(args.output))


if __name__ == "__main__":
    main()
