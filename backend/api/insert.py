"""POST /api/flow/{flow_id}/insert-step — record one forgotten action and splice
it into an existing demo at a chosen position.

Records a self-contained mini-clip (manual click-through, same engine as Manual
mode) and inserts it as a new step carrying its own `source_path`. Progress is
published on the flow's existing SSE channel; Stop reuses POST /api/stop."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.flow import _check_owner, get_flow_or_404
from core.auth import current_user
from core.progress import progress_bus
from core.storage import load_flow, log_error, save_flow
from models.flow import AuthConfig, AuthMethod
from services import browser_recorder
from services.insert_step import build_inserted_step

router = APIRouter()


class InsertStepRequest(BaseModel):
    after_index: int = -1          # insert AFTER this step index; -1 → at the front
    url: str | None = None         # where the mini-browser opens; defaults to the flow URL


def _err_text(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


async def run_insert(flow_id: str, after_index: int, url: str, insert_id: str) -> None:
    """Background task: record the mini-clip, build a step, insert it, persist."""
    try:
        await progress_bus.publish(flow_id, "recording",
                                   "Launching browser — perform the one step you forgot…", 10)
        # Use manual auth so a previously remembered sign-in (storage_state, keyed
        # by host) is reused; if the site needs no login this returns immediately.
        auth = AuthConfig(method=AuthMethod.manual)
        mp4_path, timestamps = await browser_recorder.record_insert_session(
            flow_id, url, auth, progress_bus, insert_id,
        )
        await progress_bus.publish(flow_id, "analyzing", "Building the inserted step…", 80)
        step = await build_inserted_step(flow_id, mp4_path, timestamps, url)

        # Reload the flow fresh — the user may have edited it while recording.
        flow = load_flow(flow_id)
        if flow is None:
            await progress_bus.publish_error(flow_id, "Flow not found — could not insert the step.")
            return
        pos = min(max(after_index + 1, 0), len(flow.steps))
        flow.steps.insert(pos, step)
        for i, s in enumerate(flow.steps):
            s.order = i
        save_flow(flow)
        # Terminal event: the editor reloads the flow and selects this step.
        await progress_bus.publish(flow_id, "step-inserted", step.id, 100)
    except Exception as exc:
        log_error(flow_id, "insert-step failed", exc)
        await progress_bus.publish_error(flow_id, _err_text(exc))


@router.post("/api/flow/{flow_id}/insert-step", status_code=202)
async def insert_step(flow_id: str, body: InsertStepRequest,
                      background_tasks: BackgroundTasks,
                      user: dict = Depends(current_user)):
    flow = get_flow_or_404(flow_id)
    _check_owner(flow, user)
    url = _normalize_url(body.url or flow.url)
    if not url:
        return JSONResponse({"error": "url is required"}, status_code=400)

    insert_id = uuid.uuid4().hex[:8]
    progress_bus.clear(flow_id)  # drop stale events from the original recording
    background_tasks.add_task(run_insert, flow_id, body.after_index, url, insert_id)
    return {"ok": True, "insert_id": insert_id}
