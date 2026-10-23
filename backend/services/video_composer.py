"""Per-step composition: annotated segment + TTS narration → final step clip.
Steps run with an asyncio.Semaphore(2)."""

import asyncio
import os
from pathlib import Path

from core.progress import progress_bus
from core.storage import audio_dir, clips_dir, log_error
from models.flow import DemoFlow, FlowStep
from services.annotator import annotate_clip, annotated_clip_path
from services.branding import hex_to_ffmpeg
from services.ffmpeg import drawtext, get_dimensions, get_duration, run_ffmpeg, wrap_lines
from services.tts_service import synthesize


async def _merge_audio(annotated: Path, narration: Path, out: Path, segment_duration: float,
                       *, delay: float = 0.0) -> None:
    """Play the voiceover over the clip; the clip's last frame freezes so it
    always lasts as long as the voice — no per-step settings needed. `delay`
    inserts silence before the voice starts (set by dragging the step's voice
    block on the editor timeline), so narration can land on the moment it
    describes.

    Encoded ultrafast: this is an intermediate clip that the assembler re-encodes
    again when it blends the transitions, so spending time on quality here is
    wasted — keeping it fast noticeably cuts total generate time."""
    audio_duration = await get_duration(narration)
    delay = max(0.0, delay)
    out_duration = max(segment_duration, delay + audio_duration + 0.4)
    pad = max(0.0, out_duration - segment_duration)

    a_filter = "[1:a]volume=1.3"
    delay_ms = int(delay * 1000)
    if delay_ms > 0:
        a_filter += f",adelay={delay_ms}|{delay_ms}"
    a_filter += f",apad=whole_dur={out_duration:.2f}[a]"

    await run_ffmpeg(
        [
            "-i", str(annotated),
            "-i", str(narration),
            "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.2f}[v];{a_filter}",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-b:a", "160k",
            "-t", f"{out_duration:.2f}",
            str(out),
        ],
        timeout=300.0,
    )


async def _burn_subtitles(annotated: Path, script: str, out: Path, duration: float) -> None:
    """TTS failed → silent track + narration burned in as subtitles."""
    lines = wrap_lines(script, max_chars=55, max_lines=3)
    overlays = ",".join(
        drawtext(line, fontsize=20, x="(w-text_w)/2", y=f"h-{140 - i * 32}", alpha=0.55)
        for i, line in enumerate(lines)
    ) or "null"
    await run_ffmpeg(
        [
            "-i", str(annotated),
            "-f", "lavfi", "-t", f"{duration:.2f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf", overlays,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100",
            "-t", f"{duration:.2f}",
            str(out),
        ],
        timeout=300.0,
    )


async def compose_step(flow: DemoFlow, step: FlowStep, source_video: Path,
                       video_w: int, video_h: int) -> FlowStep:
    # Steps inserted in the editor carry their own recording; everyone else
    # slices the shared session recording. The annotator scales+pads to
    # video_w/video_h regardless, so all clips stay uniform for the assembler.
    src = source_video
    if step.source_path and Path(step.source_path).exists():
        src = Path(step.source_path)
    annotated = await annotate_clip(src, step, annotated_clip_path(flow.id, step.order),
                                    video_w, video_h, brand_color=hex_to_ffmpeg(flow.brand.color))
    progress_bus.raise_if_cancelled(flow.id)

    # Prefer the user's own recorded voice for this step; fall back to TTS.
    if step.custom_voice_path and Path(step.custom_voice_path).exists():
        narration = Path(step.custom_voice_path)
    else:
        narration = await synthesize(step.narration_script,
                                     audio_dir(flow.id) / f"step_{step.order}.mp3", flow_id=flow.id,
                                     voice=getattr(flow, "voice", None))
    progress_bus.raise_if_cancelled(flow.id)

    out = clips_dir(flow.id) / f"step_{step.order}_final.mp4"
    if narration is not None:
        await _merge_audio(annotated, narration, out, step.duration, delay=step.narration_delay)
        step.audio_path = str(narration)
    else:
        log_error(flow.id, f"composer: TTS unavailable for step {step.order}, burning subtitles", "fallback")
        await _burn_subtitles(annotated, step.narration_script, out, step.duration)
        step.audio_path = None
    step.clip_path = str(out)
    return step


async def compose_all(flow: DemoFlow, source_video: Path) -> DemoFlow:
    width, height = await get_dimensions(source_video)
    # Compose several steps at once — FFmpeg is the bottleneck, so we run as many
    # in parallel as the box has cores (capped to keep memory sane).
    semaphore = asyncio.Semaphore(min(4, max(2, os.cpu_count() or 2)))
    ordered = sorted(flow.steps, key=lambda s: s.order)
    total = len(ordered)
    done = 0
    lock = asyncio.Lock()

    async def worker(step: FlowStep) -> FlowStep:
        nonlocal done
        async with semaphore:
            progress_bus.raise_if_cancelled(flow.id)
            await progress_bus.publish(flow.id, "generating",
                                       f"Annotating step {step.order + 1}/{total}…",
                                       int(done / total * 80))
            result = await compose_step(flow, step, source_video, width, height)
            async with lock:
                done += 1
            await progress_bus.publish(flow.id, "generating",
                                       f"Step {step.order + 1}/{total} composed.",
                                       int(done / total * 80))
            return result

    flow.steps = list(await asyncio.gather(*(worker(s) for s in ordered)))
    return flow
