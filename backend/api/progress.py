"""GET /api/progress/{flow_id} — SSE progress stream + job cancellation."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.progress import progress_bus

router = APIRouter()


@router.get("/api/progress/{flow_id}")
async def progress_stream(flow_id: str):
    return StreamingResponse(
        progress_bus.subscribe(flow_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/cancel/{flow_id}")
async def cancel_job(flow_id: str):
    progress_bus.request_cancel(flow_id)
    return {"cancelled": True}
