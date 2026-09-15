from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_database_url() -> str:
    db_path = (PROJECT_ROOT / "data" / "arxiv_digest.db").as_posix()
    return f"sqlite+aiosqlite:///{db_path}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    secret_key: str = "development-only-change-me"
    database_url: str = _default_database_url()
    frontend_dist_dir: Path = PROJECT_ROOT / "frontend" / "dist"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    auth_session_hours: int = 168
    auth_login_max_attempts: int = 5
    auth_lock_minutes: int = 15
    auth_cookie_secure: bool = False
    daily_run_lock_path: Path = PROJECT_ROOT / "data" / ".daily-run.lock"

    arxiv_contact_email: str = "researcher@example.com"
    arxiv_min_interval_seconds: float = 3.1
    arxiv_retry_attempts: int = 3
    arxiv_retry_base_seconds: float = 20.0
    arxiv_retry_max_seconds: float = 90.0
    arxiv_cache_ttl_seconds: int = 86_400
    arxiv_stale_cache_hours: int = 168
    arxiv_rss_cache_ttl_seconds: int = 10_800
    arxiv_rss_stale_cache_hours: int = 168
    arxiv_atom_cooldown_base_hours: int = 6
    arxiv_atom_cooldown_max_hours: int = 24
    openalex_enabled: bool = True
    openalex_max_queries_per_topic: int = 3
    openalex_cache_ttl_seconds: int = 86_400
    openalex_stale_cache_hours: int = 168
    http_timeout_seconds: float = 90.0
    llm_concurrency: int = 2
    pdf_max_pages: int = 30
    pdf_max_chars: int = 120_000
    network_time_enabled: bool = True
    network_time_servers: str = (
        "ntp.aliyun.com,time.cloudflare.com,time.google.com,pool.ntp.org"
    )
    network_time_http_urls: str = (
        "https://www.baidu.com,https://www.cloudflare.com,https://www.microsoft.com"
    )
    network_time_timeout_seconds: float = 2.5
    network_time_sync_interval_seconds: int = 1800

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def network_time_server_list(self) -> list[str]:
        return [host.strip() for host in self.network_time_servers.split(",") if host.strip()]

    @property
    def network_time_http_url_list(self) -> list[str]:
        return [url.strip() for url in self.network_time_http_urls.split(",") if url.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
