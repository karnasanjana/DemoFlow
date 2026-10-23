"""Gemini Vision: recorded session + action timestamps → demo steps with scripts.

Consecutive actions closer than MIN_STEP_SECONDS are grouped into one step
(typing email + password + clicking submit is one "Log in" step, not three
half-second steps). One frame is extracted per step; all frames go to Gemini
in a single multimodal call. Without an API key (or after a failed retry)
a deterministic fallback builds steps straight from the action descriptions.
"""

import base64
import uuid
from pathlib import Path

from core.progress import ProgressBus
from core.storage import frames_dir, log_error
from models.flow import AnimationType, BBox, FlowStep
from services.ffmpeg import get_dimensions, get_duration, run_ffmpeg
from services.gemini import extract_json, generate, has_gemini

MIN_STEP_SECONDS = 2.5
LAST_STEP_PADDING = 3.0

ANALYSIS_PROMPT = """You are analyzing a product demo screen recording.
I'm showing you {n} frames captured during an automated walkthrough of {url}.
The recording is {width}x{height} pixels.

Each frame corresponds to a segment of actions that was performed:
{segments}

For each frame (same order), create a demo step with:

name: Short step title (max 5 words)
narration_script: Compelling 1-2 sentence voiceover for a sales demo. Benefit-focused, no filler.
bbox: {{x, y, w, h}} in absolute pixels of the most important UI element (where the user clicks/looks). null if the full screen is the focus.

Return ONLY a valid JSON array with exactly {n} objects, same order as the frames. No markdown, no explanation."""


def _entry_click(entry: dict) -> tuple[float, float] | None:
    """Real click coordinate captured during recording, if any."""
    if "click_x" in entry and "click_y" in entry:
        return float(entry["click_x"]), float(entry["click_y"])
    return None


def group_actions(action_timestamps: list[dict], video_duration: float) -> list[dict]:
    """Group actions into segments of at least MIN_STEP_SECONDS.

    Returns [{start, end, descriptions: [...], click: (x, y) | None}] covering
    the recording. `click` is the last real click coordinate in the segment, so
    the demo highlight can be anchored to where the user/automation clicked
    instead of an AI guess.
    """
    if not action_timestamps:
        return [{"start": 0.0, "end": video_duration,
                 "descriptions": ["Product walkthrough"], "click": None}]

    ordered = sorted(action_timestamps, key=lambda a: a["timestamp_seconds"])
    segments: list[dict] = []
    current = {"start": ordered[0]["timestamp_seconds"],
               "descriptions": [ordered[0]["description"]], "click": _entry_click(ordered[0])}
    for entry in ordered[1:]:
        ts = entry["timestamp_seconds"]
        click = _entry_click(entry)
        if ts - current["start"] < MIN_STEP_SECONDS:
            current["descriptions"].append(entry["description"])
            if click is not None:
                current["click"] = click  # prefer the latest click in the segment
        else:
            current["end"] = ts
            segments.append(current)
            current = {"start": ts, "descriptions": [entry["description"]], "click": click}
    current["end"] = min(video_duration, current["start"] + max(LAST_STEP_PADDING, MIN_STEP_SECONDS))
    segments.append(current)

    # Clamp + drop empty segments
    cleaned = []
    for seg in segments:
        start = max(0.0, min(seg["start"], video_duration - 0.5))
        end = max(start + 1.0, min(seg["end"], video_duration))
        cleaned.append({"start": round(start, 2), "end": round(end, 2),
                        "descriptions": seg["descriptions"], "click": seg.get("click")})
    return cleaned


async def _extract_step_frames(video_path: Path, segments: list[dict], flow_id: str,
                               video_duration: float) -> list[Path]:
    out_dir = frames_dir(flow_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, seg in enumerate(segments):
        # Sample slightly after the segment start so the action's result is visible
        ts = min(seg["start"] + 0.8, max(0.0, video_duration - 0.1), (seg["start"] + seg["end"]) / 2)
        out = out_dir / f"frame_{i:03d}.jpg"
        await run_ffmpeg(["-ss", f"{ts:.2f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(out)],
                         timeout=60.0)
        paths.append(out)
    return paths


def _clamp_bbox(raw: object, width: int, height: int) -> BBox | None:
    if not isinstance(raw, dict):
        return None
    try:
        x = min(max(float(raw.get("x", 0)), 0.0), width - 20)
        y = min(max(float(raw.get("y", 0)), 0.0), height - 20)
        w = min(max(float(raw.get("w", 100)), 20.0), width - x)
        h = min(max(float(raw.get("h", 80)), 20.0), height - y)
        return BBox(x=x, y=y, w=w, h=h)
    except (TypeError, ValueError):
        return None


def _click_bbox(click: tuple[float, float], width: int, height: int) -> BBox:
    """A focused box centered on a real click point — what the highlight/ripple
    animation is anchored to. Beats the model's frame guess every time we have it."""
    cx, cy = click
    bw = min(240.0, width * 0.5)
    bh = min(140.0, height * 0.5)
    x = min(max(0.0, cx - bw / 2), width - bw)
    y = min(max(0.0, cy - bh / 2), height - bh)
    return BBox(x=round(x, 1), y=round(y, 1), w=round(bw, 1), h=round(bh, 1))


def _best_description(descriptions: list[str]) -> str:
    """Pick the most meaningful action label for a step (clicks beat waits)."""
    priorities = ["click", "type", "enter", "submit", "open", "go to", "scroll", "hover", "wait"]
    ranked = sorted(
        descriptions,
        key=lambda d: next((i for i, kw in enumerate(priorities) if kw in d.lower()), len(priorities)),
    )
    return ranked[0] if ranked else "Step"


def fallback_steps(segments: list[dict], frame_paths: list[Path],
                   width: int, height: int) -> list[FlowStep]:
    steps = []
    for i, (seg, frame) in enumerate(zip(segments, frame_paths)):
        label = _best_description(seg["descriptions"])[:50] or f"Step {i + 1}"
        click = seg.get("click")
        steps.append(
            FlowStep(
                id=uuid.uuid4().hex[:8],
                order=i,
                name=label,
                timestamp_start=seg["start"],
                timestamp_end=seg["end"],
                frame_path=str(frame),
                narration_script=f"{label} — see how effortlessly this works in the live product.",
                # Cursor + click ripple for every step; anchored to the real click point when we have one.
                animation_type=AnimationType.mouse_pointer,
                bbox=_click_bbox(click, width, height) if click else None,
                title_card_text=None,
            )
        )
    return steps


async def analyze_recording(
    flow_id: str,
    video_path: Path,
    action_timestamps: list[dict],
    progress: ProgressBus,
    url: str = "",
) -> list[FlowStep]:
    duration = await get_duration(video_path)
    width, height = await get_dimensions(video_path)

    segments = group_actions(action_timestamps, duration)
    await progress.publish(flow_id, "analyzing", f"Extracting {len(segments)} key frames…", 68)
    frame_paths = await _extract_step_frames(video_path, segments, flow_id, duration)

    if not has_gemini():
        log_error(flow_id, "vision_analyzer", "GEMINI_API_KEY not set — using deterministic steps")
        return fallback_steps(segments, frame_paths, width, height)

    segment_lines = "\n".join(
        f"Frame {i}: {seg['start']:.1f}s–{seg['end']:.1f}s — {'; '.join(seg['descriptions'][:4])}"
        for i, seg in enumerate(segments)
    )
    prompt = ANALYSIS_PROMPT.format(n=len(segments), url=url or "the product",
                                    width=width, height=height, segments=segment_lines)
    parts: list = [
        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(p.read_bytes()).decode()}}
        for p in frame_paths
    ]
    parts.append(prompt)

    raw = None
    for attempt in (1, 2):
        try:
            await progress.publish(flow_id, "analyzing", "Gemini is writing your demo script…", 75)
            candidate = extract_json(await generate(parts))
            if isinstance(candidate, list) and candidate:
                raw = candidate
                break
            raise ValueError("not a non-empty JSON array")
        except Exception as exc:
            log_error(flow_id, f"vision_analyzer: attempt {attempt} failed", exc)
            if attempt == 1:
                parts[-1] = prompt + "\n\nIMPORTANT: Reply with ONLY the raw JSON array. No other text."
    if raw is None:
        return fallback_steps(segments, frame_paths, width, height)

    steps = []
    for i, seg in enumerate(segments):
        item = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        click = seg.get("click")
        # Every step uses cursor + click ripple. Anchor it to the real recorded
        # click point when we have one (ground truth); otherwise to Gemini's bbox.
        bbox = _click_bbox(click, width, height) if click is not None \
            else _clamp_bbox(item.get("bbox"), width, height)
        steps.append(
            FlowStep(
                id=uuid.uuid4().hex[:8],
                order=i,
                name=str(item.get("name") or _best_description(seg["descriptions"])[:50] or f"Step {i + 1}")[:60],
                timestamp_start=seg["start"],
                timestamp_end=seg["end"],
                frame_path=str(frame_paths[i]),
                narration_script=str(item.get("narration_script") or "").strip()
                or f"{_best_description(seg['descriptions'])} — effortless in the live product.",
                animation_type=AnimationType.mouse_pointer,
                bbox=bbox,
                title_card_text=None,
            )
        )
    return steps
