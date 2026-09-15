import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
