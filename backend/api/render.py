"""POST /api/render — final assembly into final-demo.mp4."""

from fastapi import APIRouter, HTTPException

from core.progress import CancelledByUser, progress_bus
from core.storage import log_error, save_flow
from models.flow import FlowStatus, RenderRequest
from services.assembler import assemble
from api.flow import get_flow_or_404

router = APIRouter()


@router.post("/api/render")
async def render_final(body: RenderRequest):
    flow = get_flow_or_404(body.flow_id)
    flow.status = FlowStatus.rendering
    save_flow(flow)
    await progress_bus.publish(flow.id, "rendering", "Assembling final video…", 82)

    try:
        out_path = await assemble(flow)
        flow.status = FlowStatus.done
        flow.output_video_path = str(out_path)
        save_flow(flow)
        await progress_bus.publish(flow.id, "done", "Your demo is ready!", 100)
        return {"output_video_path": str(out_path)}
    except CancelledByUser:
        flow.status = FlowStatus.ready
        save_flow(flow)
        await progress_bus.publish(flow.id, "cancelled", "Rendering cancelled.", 0)
        raise HTTPException(499, "cancelled")
    except Exception as exc:
        log_error(flow.id, "render failed", exc)
        flow.status = FlowStatus.error
        flow.error = str(exc)
        save_flow(flow)
        await progress_bus.publish(flow.id, "error", str(exc), 0)
        raise HTTPException(500, str(exc))
