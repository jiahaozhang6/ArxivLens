from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import SessionLocal, close_db, init_db
from app.routers import auth, chats, jobs, papers, profiles, settings, system, topics
from app.services.paper_chat import recover_interrupted_chats
from app.services.pipeline import recover_interrupted_runs
from app.services.settings_service import ensure_default_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    await recover_interrupted_runs()
    await recover_interrupted_chats()
    async with SessionLocal() as session:
        await ensure_default_settings(session)
    yield
    await close_db()


app = FastAPI(
    title="arXiv Research Digest API",
    version="0.1.0",
    lifespan=lifespan,
)
settings_config = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings_config.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(system.router)
app.include_router(papers.router)
app.include_router(chats.router)
app.include_router(topics.router)
app.include_router(profiles.router)
app.include_router(settings.router)
app.include_router(jobs.router)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if settings_config.auth_cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.get("/api/health")
async def health():
    return {"status": "ok"}


if settings_config.frontend_dist_dir.exists():
    app.mount(
        "/",
        StaticFiles(directory=settings_config.frontend_dist_dir, html=True),
        name="frontend",
    )
