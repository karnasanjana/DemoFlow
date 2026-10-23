"""POST /api/regenerate-script — rewrite one step's narration with Gemini."""

from fastapi import APIRouter, HTTPException

from core.storage import log_error, save_flow
from models.flow import RegenerateScriptRequest
from services.script_writer import rewrite_step_script
from api.flow import get_flow_or_404

router = APIRouter()


@router.post("/api/regenerate-script")
async def regenerate_script(body: RegenerateScriptRequest):
    flow = get_flow_or_404(body.flow_id)
    step = next((s for s in flow.steps if s.id == body.step_id), None)
    if step is None:
        raise HTTPException(404, "Step not found")
    tone = body.tone if body.tone in ("professional", "casual", "executive") else "professional"
    try:
        step.narration_script = await rewrite_step_script(flow.title, step, tone, draft=body.text)
        save_flow(flow)
        return {"step_id": step.id, "narration_script": step.narration_script}
    except Exception as exc:
        log_error(flow.id, "regenerate-script failed", exc)
        raise HTTPException(502, str(exc))
