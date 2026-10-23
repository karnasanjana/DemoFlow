"""FFmpeg annotation on top of the real recording: cut a step's segment and
overlay the chosen animation.

The curated set — each effect is anchored to the cursor / click point (the bbox
center, which for click steps is the exact recorded click):

  mouse_pointer — live recorded cursor + a brand-colored click ripple at the point
  zoom          — smooth zoom punch into the click point
  highlight     — pulsing brand-color border box around the target
  title_card    — large centered chapter text over the footage

Legacy types (spotlight, click_ripple, fade) still render for old saved flows but
are no longer offered in the editor.
A small chapter caption is layered on any type when title_card_text is set.

Cut + filter happen in one FFmpeg pass (re-encode is required for filters
anyway; single pass keeps cuts frame-accurate).
"""

from pathlib import Path

from core.storage import clips_dir
from models.flow import AnimationType, BBox, FlowStep
from services.ffmpeg import drawtext, run_ffmpeg

BRAND_COLOR = "0x7F77DD"


def _px(step_bbox: BBox | None, width: int, height: int) -> tuple[int, int, int, int]:
    """Clamped absolute-pixel bbox; defaults to a centered region when absent."""
    if step_bbox is None:
        return int(width * 0.2), int(height * 0.2), int(width * 0.6), int(height * 0.55)
    x = min(max(0, int(step_bbox.x)), width - 16)
    y = min(max(0, int(step_bbox.y)), height - 16)
    w = min(max(16, int(step_bbox.w)), width - x)
    h = min(max(16, int(step_bbox.h)), height - y)
    return x // 2 * 2, y // 2 * 2, w // 2 * 2, h // 2 * 2


def _ripple_effect(cx: int, cy: int, width: int, height: int, color: str) -> str:
    """Expanding ring that pulses at (cx, cy) — anchors a click animation to the
    cursor point. Used by both mouse_pointer (with the live cursor) and the legacy
    click_ripple type."""
    rcx = min(max(cx, 50), width - 50)
    rcy = min(max(cy, 50), height - 50)
    return (
        f"drawbox=x={rcx - 22}:y={rcy - 22}:w=44:h=44:color={color}@0.9:t=4:"
        f"enable='lt(mod(t,1.5),0.4)',"
        f"drawbox=x={rcx - 42}:y={rcy - 42}:w=84:h=84:color={color}@0.4:t=3:"
        f"enable='between(mod(t,1.5),0.2,0.7)'"
    )


def build_annotation_filter(step: FlowStep, width: int, height: int,
                            brand_color: str = BRAND_COLOR) -> str:
    """Pure function: step spec → -vf chain. brand_color tints highlight/spotlight."""
    duration = step.duration
    bx, by, bw, bh = _px(step.bbox, width, height)
    cx, cy = bx + bw // 2, by + bh // 2

    # Check if we have cuts to apply
    select_filter = None
    if getattr(step, "cuts", None):
        step_dur = step.timestamp_end - step.timestamp_start
        rel_cuts = []
        for c in step.cuts:
            c_start = max(0.0, min(c.start - step.timestamp_start, step_dur))
            c_end = max(0.0, min(c.end - step.timestamp_start, step_dur))
            if c_end > c_start:
                rel_cuts.append((c_start, c_end))
        if rel_cuts:
            rel_cuts.sort(key=lambda x: x[0])
            merged_cuts = []
            for c in rel_cuts:
                if merged_cuts and c[0] <= merged_cuts[-1][1]:
                    merged_cuts[-1] = (merged_cuts[-1][0], max(merged_cuts[-1][1], c[1]))
                else:
                    merged_cuts.append(c)
            kept = []
            curr = 0.0
            for c_start, c_end in merged_cuts:
                if c_start > curr:
                    kept.append((curr, c_start))
                curr = c_end
            if curr < step_dur:
                kept.append((curr, step_dur))
            if not kept:
                kept = [(0.0, 0.1)]
            expr = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in kept)
            select_filter = f"select='{expr}',setpts=N/FRAME_RATE/TB"

    base = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
    )
    if select_filter:
        base = f"{select_filter},{base}"

    t = step.animation_type
    if t is AnimationType.spotlight:
        effect = (
            f"split=2[bg][fg];"
            f"[bg]eq=brightness=-0.35:saturation=0.45[dark];"
            f"[fg]crop={bw}:{bh}:{bx}:{by}[bright];"
            f"[dark][bright]overlay={bx}:{by},"
            f"drawbox=x={bx}:y={by}:w={bw}:h={bh}:color={brand_color}@0.85:t=3"
        )
    elif t is AnimationType.highlight:
        effect = (
            f"drawbox=x={bx}:y={by}:w={bw}:h={bh}:color={brand_color}@0.9:t=4,"
            f"drawbox=x={max(0, bx - 6)}:y={max(0, by - 6)}:"
            f"w={min(width - max(0, bx - 6), bw + 12)}:h={min(height - max(0, by - 6), bh + 12)}:"
            f"color={brand_color}@0.35:t=3:enable='lt(mod(t,1.2),0.7)'"
        )
    elif t is AnimationType.zoom:
        # Smooth punch-in centered on the selected box. A naive zoompan shimmers
        # ("shaky" zoom) because it rounds the crop window's x/y to whole pixels
        # every frame, and while the zoom is still growing those rounded edges
        # jitter by ±1px. Two fixes remove it:
        #   1. Supersample — scale the frame up by `sup` before zoompan so each
        #      1px rounding step lands on a sub-pixel position once scaled back
        #      down, smoothing the motion.
        #   2. Ramp then hold — punch in over a short ramp (~0.6s) and then hold
        #      a fixed zoom. A static zoom has nothing to round frame-to-frame,
        #      so the bulk of the clip is rock-steady.
        zf = 1.8
        sup = 2  # supersample factor
        scx, scy = cx * sup, cy * sup
        ramp_frames = max(1, int(min(0.6, max(0.1, duration)) * 30))
        rate = (zf - 1.0) / ramp_frames
        effect = (
            f"scale={width * sup}:{height * sup}:flags=bicubic,"
            f"zoompan=z='min(pzoom+{rate:.6f},{zf})':d=1:"
            f"x='min(max({scx}-(iw/zoom/2),0),iw-iw/zoom)':"
            f"y='min(max({scy}-(ih/zoom/2),0),ih-ih/zoom)':"
            f"s={width}x{height}:fps=30"
        )
    elif t is AnimationType.crop:
        # Static crop to selected region scaled to full frame
        effect = (
            f"crop={bw}:{bh}:{bx}:{by},"
            f"scale={width}:{height}"
        )
    elif t is AnimationType.mouse_pointer:
        # The recording already shows the live mouse cursor AND a yellow click
        # ring (both injected during capture in browser_recorder), so the click
        # already reads as a designed animation. We deliberately draw NOTHING
        # extra here: the old brand-colored drawbox ripple pulsed on a fixed
        # 1.5s timer — not the real click — so it flashed a coloured box on the
        # screen before the click actually happened. The recorded ring is the
        # one true click animation.
        effect = "null"
    elif t is AnimationType.click_ripple:  # legacy: yellow ripple, no live cursor emphasis
        effect = _ripple_effect(cx, cy, width, height, "yellow")
    elif t is AnimationType.title_card:
        text = step.title_card_text or step.name
        effect = drawtext(text, fontsize=40, x="(w-text_w)/2", y="(h/2)-30",
                          enable=f"between(t,0.3,{max(1.0, duration - 0.3):.2f})", alpha=0.65, boxborder=18)
    else:  # fade
        fade_out_start = max(0.5, duration - 0.5)
        effect = f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out_start:.2f}:d=0.5"

    chain = f"{base},{effect}"

    # Chapter caption for any non-title-card animation
    if step.title_card_text and t is not AnimationType.title_card:
        show_until = max(1.0, min(duration - 0.2, 4.5))
        chain += "," + drawtext(step.title_card_text, fontsize=28, x="(w-text_w)/2", y="h-90",
                                enable=f"between(t,0.3,{show_until:.2f})", alpha=0.65, boxborder=14)
    return chain


async def annotate_clip(
    source_video: Path,
    step: FlowStep,
    output_path: Path,
    video_w: int = 1280,
    video_h: int = 800,
    brand_color: str = BRAND_COLOR,
) -> Path:
    """Cut [timestamp_start → timestamp_end] and apply the animation (video-only)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await run_ffmpeg(
        [
            "-ss", f"{step.timestamp_start:.3f}",
            "-i", str(source_video),
            "-t", f"{max(0.1, step.timestamp_end - step.timestamp_start):.3f}",
            "-vf", build_annotation_filter(step, video_w, video_h, brand_color),
            "-an",
            # Intermediate clip — re-encoded again during final assembly, so encode
            # it fast rather than spending time on quality that gets thrown away.
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            str(output_path),
        ],
        timeout=300.0,
    )
    return output_path


def annotated_clip_path(flow_id: str, step_order: int) -> Path:
    return clips_dir(flow_id) / f"step_{step_order}_annotated.mp4"
