"""Build a FlowStep from a self-contained insert recording.

The editor's "Insert step here" records just the one forgotten action into its
own little video (see browser_recorder.record_insert_session). This turns that
recording into a FlowStep whose footage is the recording itself (source_path),
with timestamps relative to it. Name/narration are derived deterministically
from the captured actions — the user refines them in the editor.
"""

import uuid
from pathlib import Path

from core.storage import frames_dir, log_error
from models.flow import AnimationType, FlowStep
from services.ffmpeg import get_dimensions, get_duration, run_ffmpeg
from services.vision_analyzer import _best_description, _click_bbox, _entry_click


async def build_inserted_step(
    flow_id: str, mp4_path: Path, action_timestamps: list[dict], url: str = "",
) -> FlowStep:
    """Create a FlowStep backed by its own recording (`mp4_path`). `order` is set
    by the caller after insertion."""
    duration = await get_duration(mp4_path)
    width, height = await get_dimensions(mp4_path)

    descriptions = [a.get("description", "") for a in action_timestamps if a.get("description")]
    name = (_best_description(descriptions)[:50] if descriptions else "New step") or "New step"

    # Anchor the click animation to the last recorded click, if any.
    click = None
    for entry in action_timestamps:
        c = _entry_click(entry)
        if c is not None:
            click = c
    bbox = _click_bbox(click, width, height) if click is not None else None

    # One representative frame, sampled slightly after the start so the action's
    # result is visible. Unique filename so it never collides with analysis frames.
    step_id = uuid.uuid4().hex[:8]
    out_dir = frames_dir(flow_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = out_dir / f"insert_{step_id}.jpg"
    ts = min(max(0.0, duration - 0.1), 0.8 if duration > 1.0 else duration / 2)
    try:
        await run_ffmpeg(
            ["-ss", f"{ts:.2f}", "-i", str(mp4_path), "-frames:v", "1", "-q:v", "2", str(frame)],
            timeout=60.0,
        )
    except Exception as exc:
        log_error(flow_id, "insert_step: frame extraction failed", exc)

    return FlowStep(
        id=step_id,
        order=0,  # caller reindexes after insertion
        name=name,
        timestamp_start=0.0,
        timestamp_end=round(duration, 2),
        frame_path=str(frame),
        narration_script=f"{name} — see how effortlessly this works in the live product.",
        animation_type=AnimationType.mouse_pointer,
        bbox=bbox,
        title_card_text=None,
        source_path=str(mp4_path),
    )
