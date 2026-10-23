"""GET/PATCH /api/flow/{flow_id} + generated asset serving."""

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core.auth import current_user
from core.storage import (
    audio_dir, delete_flow_dir, final_video_path, flow_dir, frames_dir, list_flows,
    load_flow, recording_dir, save_flow,
)
from models.flow import DemoFlow, FlowStatus, FlowUpdateRequest, PreviewAudioRequest
from services.branding import list_music, logo_path
from services.tts_service import synthesize

router = APIRouter()


def _check_owner(flow: DemoFlow, user: dict) -> None:
    """Legacy flows (no owner) stay accessible to any signed-in user; owned flows
    are restricted to their owner."""
    if flow.owner_id is not None and flow.owner_id != user["id"]:
        raise HTTPException(403, "This demo belongs to another account.")


def _effective_label(flow: DemoFlow) -> str:
    """The category/folder a demo is grouped under. A user-set label wins; otherwise
    fall back to the website's domain so demos of the same site land together."""
    label = (getattr(flow, "label", "") or "").strip()
    if label:
        return label
    host = (urlparse(flow.url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "Other"


def _flow_summary(flow: DemoFlow) -> dict:
    steps = flow.steps or []
    thumb = None
    if steps and steps[0].frame_path:
        fname = steps[0].frame_path.replace("\\", "/").split("/")[-1]
        thumb = f"/api/output/{flow.id}/frame/{fname}"
    return {
        "id": flow.id,
        "title": flow.title or "Untitled demo",
        "url": flow.url,
        "label": _effective_label(flow),
        "user_label": (getattr(flow, "label", "") or "").strip(),  # explicit label, "" if grouping by website
        "status": flow.status,
        "created_at": flow.created_at.isoformat() if hasattr(flow.created_at, "isoformat") else str(flow.created_at),
        "step_count": len(steps),
        "thumbnail": thumb,
        "has_video": final_video_path(flow.id).exists(),
    }

_LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MAX_LOGO_BYTES = 4 * 1024 * 1024
_MAX_VOICE_BYTES = 25 * 1024 * 1024
_VOICE_EXTS = ("webm", "ogg", "m4a", "mp3", "wav")


def _voice_ext(content_type: str | None) -> str:
    ct = (content_type or "").lower()
    if "ogg" in ct:
        return "ogg"
    if "mp4" in ct or "m4a" in ct or "aac" in ct:
        return "m4a"
    if "wav" in ct:
        return "wav"
    if "mpeg" in ct or "mp3" in ct:
        return "mp3"
    return "webm"  # MediaRecorder default (audio/webm;codecs=opus)


def get_flow_or_404(flow_id: str) -> DemoFlow:
    flow = load_flow(flow_id)
    if flow is None:
        raise HTTPException(404, "Flow not found")
    return flow


def _safe(segment: str) -> str:
    if not segment or "/" in segment or "\\" in segment or ".." in segment:
        raise HTTPException(400, "Bad path segment")
    return segment


@router.get("/api/flows")
async def my_flows(user: dict = Depends(current_user)) -> dict:
    """Dashboard: the signed-in user's saved demos, newest first."""
    return {"flows": [_flow_summary(f) for f in list_flows(owner_id=user["id"])]}


def _reconcile_video_status(flow: DemoFlow) -> DemoFlow:
    """Keep status truthful about whether a shareable video exists.

    A finished render can leave status at 'ready' — e.g. a later /api/generate
    (regenerate) resets it without re-rendering, or a render is interrupted after
    the file is written. If final-demo.mp4 exists on disk, the demo IS generated
    and shareable, so persist status='done'. This stops reloads from wrongly
    asking the user to "generate first" for a demo that already has a video.
    """
    fv = final_video_path(flow.id)
    if fv.exists() and flow.status == FlowStatus.ready:
        flow.status = FlowStatus.done
        flow.output_video_path = str(fv)
        save_flow(flow)
    return flow


@router.get("/api/flow/{flow_id}")
async def get_flow(flow_id: str, user: dict = Depends(current_user)) -> DemoFlow:
    flow = get_flow_or_404(_safe(flow_id))
    _check_owner(flow, user)
    return _reconcile_video_status(flow)


@router.delete("/api/flow/{flow_id}")
async def delete_flow(flow_id: str, user: dict = Depends(current_user)) -> dict:
    flow = get_flow_or_404(_safe(flow_id))
    _check_owner(flow, user)
    delete_flow_dir(flow.id)
    return {"ok": True}


@router.patch("/api/flow/{flow_id}")
async def update_flow(flow_id: str, body: FlowUpdateRequest,
                      user: dict = Depends(current_user)) -> DemoFlow:
    flow = get_flow_or_404(_safe(flow_id))
    _check_owner(flow, user)
    if body.title is not None:
        flow.title = body.title.strip()[:80] or flow.title
    if body.description is not None:
        flow.description = body.description.strip()[:300]
    if body.label is not None:
        flow.label = body.label.strip()[:60]
    if body.steps is not None:
        ordered = sorted(body.steps, key=lambda s: s.order)
        for i, step in enumerate(ordered):
            step.order = i
        flow.steps = ordered
    if body.brand is not None:
        flow.brand = body.brand
    if body.voice is not None:
        flow.voice = body.voice
    save_flow(flow)
    return flow


def _unique_copy_title(base: str, user_id: str | None) -> str:
    """A distinct title for a copy: "Hello" → "Hello(1)", and duplicating a copy
    increments — "Hello(1)" → "Hello(2)" — never colliding with existing titles."""
    existing = {(f.title or "").strip() for f in list_flows(owner_id=user_id)}
    base = (base or "Untitled demo").strip()
    m = re.match(r"^(.*?)\s*\((\d+)\)\s*$", base)   # strip a trailing "(n)" if present
    root = (m.group(1).strip() if m else base) or "Untitled demo"
    n = 1
    while f"{root}({n})" in existing:
        n += 1
    return f"{root}({n})"


@router.post("/api/flow/{flow_id}/duplicate")
async def duplicate_flow(flow_id: str, user: dict = Depends(current_user)) -> DemoFlow:
    """Clone a demo (all assets + edits) into a new one named "<name>(n)"."""
    src = get_flow_or_404(_safe(flow_id))
    _check_owner(src, user)
    new_id = uuid.uuid4().hex[:10]
    src_dir, dst_dir = flow_dir(src.id), flow_dir(new_id)
    if src_dir.exists():
        shutil.copytree(src_dir, dst_dir)
    else:
        dst_dir.mkdir(parents=True, exist_ok=True)
    # The source id appears in the id field and in every absolute asset path
    # (frames/audio/clips/recording/final video) stored in flow.json, so a plain
    # id→new_id swap re-points the whole copy at its own directory.
    fj = dst_dir / "flow.json"
    dup = DemoFlow.model_validate(json.loads(fj.read_text(encoding="utf-8").replace(src.id, new_id)))
    dup.id = new_id
    dup.owner_id = user["id"]
    dup.created_at = datetime.now(timezone.utc)
    dup.title = _unique_copy_title(src.title or "Untitled demo", user["id"])
    save_flow(dup)
    return dup


@router.get("/api/music")
async def available_music() -> dict:
    """Background-music tracks the user can choose from."""
    return {"tracks": list_music()}


@router.post("/api/flow/{flow_id}/preview-audio")
async def preview_audio(flow_id: str, body: PreviewAudioRequest):
    """Synthesize the given narration (the live, possibly-unsaved text) to MP3 so
    the editor can play it over the clip and the user can dial in the sync.
    Returned uncached so each edit re-synthesizes."""
    flow = get_flow_or_404(_safe(flow_id))
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "No narration text to preview")
    safe_step = _safe(body.step_id) if body.step_id else "preview"
    out = audio_dir(flow.id) / f"preview_{safe_step}.mp3"
    result = await synthesize(text[:1500], out, flow_id=flow.id, voice=body.voice or flow.voice)
    if result is None:
        raise HTTPException(502, "Text-to-speech is unavailable on this server")
    return FileResponse(result, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/flow/{flow_id}/logo")
async def upload_logo(flow_id: str, file: UploadFile = File(...)) -> DemoFlow:
    """Store a brand logo for the flow; it's watermarked onto the final video."""
    flow = get_flow_or_404(_safe(flow_id))
    ext = _LOGO_TYPES.get(file.content_type or "")
    if ext is None:
        raise HTTPException(400, "Logo must be a PNG, JPEG, or WebP image")
    data = await file.read()
    if len(data) > _MAX_LOGO_BYTES:
        raise HTTPException(400, "Logo too large (max 4 MB)")
    # Remove any prior logo of a different extension so resolution is unambiguous.
    for old in ("png", "jpg", "jpeg", "webp"):
        (flow_dir(flow.id) / f"logo.{old}").unlink(missing_ok=True)
    dest = flow_dir(flow.id) / f"logo.{ext}"
    dest.write_bytes(data)
    flow.brand.logo_path = str(dest)
    save_flow(flow)
    return flow


@router.delete("/api/flow/{flow_id}/logo")
async def delete_logo(flow_id: str) -> DemoFlow:
    flow = get_flow_or_404(_safe(flow_id))
    for ext in ("png", "jpg", "jpeg", "webp"):
        (flow_dir(flow.id) / f"logo.{ext}").unlink(missing_ok=True)
    flow.brand.logo_path = None
    save_flow(flow)
    return flow


@router.get("/api/output/{flow_id}/logo")
async def serve_logo(flow_id: str):
    path = logo_path(_safe(flow_id))
    if path is None:
        raise HTTPException(404, "No logo uploaded")
    return FileResponse(path)


# ---- per-step custom voiceover (user mic recording) ----

def _voice_path(flow_id: str, step_id: str) -> "object | None":
    for ext in _VOICE_EXTS:
        p = audio_dir(flow_id) / f"voice_{step_id}.{ext}"
        if p.exists():
            return p
    return None


@router.post("/api/flow/{flow_id}/step/{step_id}/voice")
async def upload_step_voice(flow_id: str, step_id: str,
                            file: UploadFile = File(...),
                            user: dict = Depends(current_user)) -> DemoFlow:
    """Store a user-recorded voiceover for one step; used instead of TTS at render."""
    flow = get_flow_or_404(_safe(flow_id))
    _check_owner(flow, user)
    sid = _safe(step_id)
    step = next((s for s in flow.steps if s.id == sid), None)
    if step is None:
        raise HTTPException(404, "Step not found")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty recording")
    if len(data) > _MAX_VOICE_BYTES:
        raise HTTPException(400, "Recording too large (max 25 MB)")
    for ext in _VOICE_EXTS:  # clear any prior recording so resolution is unambiguous
        (audio_dir(flow.id) / f"voice_{sid}.{ext}").unlink(missing_ok=True)
    dest = audio_dir(flow.id) / f"voice_{sid}.{_voice_ext(file.content_type)}"
    dest.write_bytes(data)
    step.custom_voice_path = str(dest)
    save_flow(flow)
    return flow


@router.delete("/api/flow/{flow_id}/step/{step_id}/voice")
async def delete_step_voice(flow_id: str, step_id: str,
                            user: dict = Depends(current_user)) -> DemoFlow:
    flow = get_flow_or_404(_safe(flow_id))
    _check_owner(flow, user)
    sid = _safe(step_id)
    step = next((s for s in flow.steps if s.id == sid), None)
    if step is None:
        raise HTTPException(404, "Step not found")
    for ext in _VOICE_EXTS:
        (audio_dir(flow.id) / f"voice_{sid}.{ext}").unlink(missing_ok=True)
    step.custom_voice_path = None
    save_flow(flow)
    return flow


@router.get("/api/output/{flow_id}/step-voice/{step_id}")
async def serve_step_voice(flow_id: str, step_id: str):
    """Play back a step's recorded voiceover in the editor."""
    path = _voice_path(_safe(flow_id), _safe(step_id))
    if path is None:
        raise HTTPException(404, "No recording for this step")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@router.get("/api/output/{flow_id}/final-demo.mp4")
async def download_final(flow_id: str):
    path = final_video_path(_safe(flow_id))
    if not path.exists():
        raise HTTPException(404, "Video not rendered yet")
    # no-store: re-exports overwrite this same path/URL, so without it the browser
    # (and download) can replay a stale earlier render.
    return FileResponse(path, media_type="video/mp4", filename="demo.mp4",
                        content_disposition_type="inline",
                        headers={"Cache-Control": "no-store"})


@router.get("/api/output/{flow_id}/recording.mp4")
async def serve_recording(flow_id: str):
    """Raw session recording — used by the editor's live step preview.
    FileResponse handles Range headers, so the <video> element can seek."""
    path = recording_dir(_safe(flow_id)) / "session.mp4"
    if not path.exists():
        raise HTTPException(404, "Recording not found")
    return FileResponse(path, media_type="video/mp4")


@router.get("/api/output/{flow_id}/step-source/{step_id}")
async def serve_step_source(flow_id: str, step_id: str):
    """An inserted step's own mini-recording — used by the editor preview when a
    step carries its own footage (source_path) instead of slicing session.mp4.
    FileResponse handles Range headers so the <video> element can seek."""
    flow = get_flow_or_404(_safe(flow_id))
    sid = _safe(step_id)
    step = next((s for s in flow.steps if s.id == sid), None)
    if step is None or not step.source_path:
        raise HTTPException(404, "No source recording for this step")
    from pathlib import Path
    path = Path(step.source_path)
    if not path.exists():
        raise HTTPException(404, "Source recording file missing")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@router.get("/api/output/{flow_id}/frame/{filename}")
async def serve_frame(flow_id: str, filename: str):
    flow_id, filename = _safe(flow_id), _safe(filename)
    if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
        raise HTTPException(400, "Bad frame filename")
    path = frames_dir(flow_id) / filename
    if not path.exists():
        raise HTTPException(404, "Frame not found")
    return FileResponse(path, media_type="image/jpeg")
