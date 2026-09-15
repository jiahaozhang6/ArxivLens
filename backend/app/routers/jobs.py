import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_session
from app.models import RunLog
from app.schemas import ManualRunRequest, RunLogOut
from app.services.pipeline import RunAlreadyActive, execute_daily_run, start_daily_run

router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_admin)],
)
_running_tasks: set[asyncio.Task] = set()


def _retain_task(task: asyncio.Task) -> None:
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)


@router.get("/runs", response_model=list[RunLogOut])
async def list_runs(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    limit = min(max(limit, 1), 200)
    return list(
        await session.scalars(select(RunLog).order_by(RunLog.started_at.desc()).limit(limit))
    )


@router.get("/runs/{run_id}", response_model=RunLogOut)
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)):
    run = await session.get(RunLog, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(payload: ManualRunRequest):
    try:
        run_id = await start_daily_run(trigger="manual")
    except RunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    task = asyncio.create_task(
        execute_daily_run(
            run_id,
            topic_ids=payload.topic_ids,
            send_email=payload.send_email,
        ),
        name=f"manual-arxiv-run-{run_id}",
    )
    _retain_task(task)
    return {"run_id": run_id, "status": "running"}
