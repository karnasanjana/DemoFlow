"""POST /api/record — the main endpoint.

Parses instructions → records the browser session → analyzes the recording.
Returns {flow_id} immediately; progress streams over SSE."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse

from core.auth import current_user
from core.progress import progress_bus
from core.storage import ensure_flow_dirs, load_flow, log_error, save_flow
from models.flow import DemoFlow, FlowStatus, RecordMode, RecordRequest
from services import browser_recorder, instruction_parser, vision_analyzer
from services.script_writer import generate_title

router = APIRouter()


def _err_text(exc: BaseException) -> str:
    """Human-readable error — some exceptions (e.g. NotImplementedError) have
    an empty str(), which would surface as a blank message in the UI."""
    return str(exc).strip() or type(exc).__name__


async def run_pipeline(flow_id: str, req: RecordRequest) -> None:
    """Parse → record → analyze. Fully automatic and fault-tolerant: a single
    failing click never crashes the run, and a recording that was captured is
    always persisted (so analysis can be retried without re-recording)."""
    flow = load_flow(flow_id)
    if flow is None:
        await progress_bus.publish_error(flow_id, "Flow not found — could not start pipeline.")
        return

    try:
        if req.mode == RecordMode.manual:
            # Manual mode: no instructions — the user drives the browser.
            flow.status = FlowStatus.recording
            save_flow(flow)
            await progress_bus.publish(flow_id, "recording",
                                       "Launching browser — click through your product yourself…", 10)
            video_path, timestamps = await browser_recorder.record_manual_session(
                flow_id, req.url, req.auth, progress_bus,
                req.viewport_width, req.viewport_height,
            )
        else:
            # 1. Parse instructions into structured browser actions.
            await progress_bus.publish(flow_id, "parsing", "Parsing your instructions…", 5)
            actions = await instruction_parser.parse(req.instructions, req.url, req.auth, flow_id)
            await progress_bus.publish(flow_id, "parsing", f"Planned {len(actions)} browser actions.", 8)

            # 2. Record the session (auth runs first, then every instruction, automatically).
            flow.status = FlowStatus.recording
            save_flow(flow)
            await progress_bus.publish(flow_id, "recording",
                                       "Launching browser — automation is running, do not interact…", 10)
            video_path, timestamps = await browser_recorder.record_session(
                flow_id, req.url, actions, req.auth, progress_bus,
                req.viewport_width, req.viewport_height,
            )

        # Persist the raw recording immediately — if analysis fails below, the
        # video is not lost and the user can retry analysis.
        flow.raw_recording_path = str(video_path)
        flow.status = FlowStatus.analyzing
        save_flow(flow)
    except Exception as exc:
        # Parsing or recording failed — nothing usable was produced.
        log_error(flow_id, "record pipeline failed (parse/record)", exc)
        if flow:
            flow.status = FlowStatus.error
            flow.error = _err_text(exc)
            save_flow(flow)
        await progress_bus.publish_error(flow_id, _err_text(exc))
        return

    try:
        # 3. Analyze the recording → steps + narration.
        await progress_bus.publish(flow_id, "analyzing", "Analyzing recording with Gemini…", 65)
        steps = await vision_analyzer.analyze_recording(flow_id, video_path, timestamps,
                                                        progress_bus, url=req.url)

        # 4. Ready for editing.
        flow.steps = steps
        flow.title = await generate_title(steps, req.url)
        flow.status = FlowStatus.ready
        save_flow(flow)
        await progress_bus.publish(flow_id, "ready",
                                   f"Flow ready with {len(steps)} steps — opening editor!", 100)
    except Exception as exc:
        # Recording succeeded but analysis failed: keep the raw video so the
        # run can be retried, and report the error clearly.
        log_error(flow_id, "record pipeline failed (analysis) — raw recording kept", exc)
        flow.status = FlowStatus.error
        flow.error = f"Analysis failed (recording saved): {_err_text(exc)}"
        save_flow(flow)
        await progress_bus.publish_error(flow_id, flow.error)


@router.post("/api/record", status_code=202)
async def start_recording(req: RecordRequest, background_tasks: BackgroundTasks,
                          user: dict = Depends(current_user)):
    url = req.url.strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    if not url:
        return JSONResponse({"error": "url is required"}, status_code=400)
    if req.mode == RecordMode.auto and not req.instructions.strip():
        return JSONResponse({"error": "instructions are required in automated mode"}, status_code=400)
    req.url = url

    flow_id = uuid.uuid4().hex[:10]
    ensure_flow_dirs(flow_id)
    progress_bus.clear(flow_id)

    flow = DemoFlow(id=flow_id, owner_id=user["id"], url=url,
                    instructions=req.instructions, status=FlowStatus.parsing)
    save_flow(flow)
    background_tasks.add_task(run_pipeline, flow_id, req)
    return {"flow_id": flow_id, "status": flow.status}


@router.post("/api/stop/{flow_id}")
async def stop_recording(flow_id: str):
    """Manual mode: finish the recording and continue to analysis."""
    progress_bus.request_stop(flow_id)
    return {"ok": True}


@router.post("/api/continue/{flow_id}")
async def continue_after_login(flow_id: str):
    """Manual sign-in: the user has logged in by hand — resume the run."""
    progress_bus.request_continue(flow_id)
    return {"ok": True}
