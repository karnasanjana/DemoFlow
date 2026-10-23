"""POST /api/generate — annotate + narrate + compose every step clip."""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from core.progress import CancelledByUser, progress_bus
from core.storage import log_error, save_flow
from models.flow import FlowStatus, GenerateRequest
from services.video_composer import compose_all
from api.flow import get_flow_or_404

router = APIRouter()


@router.post("/api/generate")
async def generate_clips(body: GenerateRequest):
    flow = get_flow_or_404(body.flow_id)
    if not flow.steps:
        raise HTTPException(400, "Flow has no steps to generate")
    if not flow.raw_recording_path or not Path(flow.raw_recording_path).exists():
        raise HTTPException(400, "Recording not found — run /api/record first")

    flow.status = FlowStatus.generating
    save_flow(flow)
    progress_bus.clear_cancel(flow.id)
    await progress_bus.publish(flow.id, "generating", "Starting per-step annotation…", 2)

    try:
        flow = await compose_all(flow, Path(flow.raw_recording_path))
        save_flow(flow)
        await progress_bus.publish(flow.id, "generating", "All step clips composed.", 80)
        return flow
    except CancelledByUser:
        flow.status = FlowStatus.ready
        save_flow(flow)
        await progress_bus.publish(flow.id, "cancelled", "Generation cancelled.", 0)
        raise HTTPException(499, "cancelled")
    except Exception as exc:
        log_error(flow.id, "generate failed", exc)
        flow.status = FlowStatus.error
        flow.error = str(exc)
        save_flow(flow)
        await progress_bus.publish(flow.id, "error", str(exc), 0)
        raise HTTPException(500, str(exc))
