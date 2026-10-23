"""Async FFmpeg/ffprobe subprocess helpers shared by all video services.

Subprocesses are spawned with subprocess.run in a worker thread rather than
asyncio.create_subprocess_exec: on Windows, uvicorn --reload forces a
SelectorEventLoop which cannot spawn subprocesses (NotImplementedError), and
the thread approach works on every loop and platform.

GCP MIGRATION: These run unchanged inside a Cloud Run Job container with
ffmpeg baked into the image.
"""

import asyncio
import json
import platform
import shutil
import subprocess
import textwrap
from pathlib import Path


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run a command in a worker thread; raises RuntimeError on timeout."""
    try:
        return await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout}s: {' '.join(cmd[:7])}…")


def ffmpeg_install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "brew install ffmpeg"
    if system == "Windows":
        return "winget install ffmpeg"
    return "sudo apt install ffmpeg"


async def run_ffmpeg(args: list[str], timeout: float = 600.0) -> None:
    """Run ffmpeg with -y; raise RuntimeError with stderr tail on failure."""
    proc = await _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode(errors="replace")[-2000:]
        raise RuntimeError(f"ffmpeg exited {proc.returncode}:\n{tail}")


async def run_ffmpeg_capture(args: list[str], timeout: float = 600.0) -> str:
    """Run ffmpeg and return its stderr text (for showinfo/scene parsing)."""
    proc = await _run(["ffmpeg", "-y", "-hide_banner", *args], timeout)
    return (proc.stderr or b"").decode(errors="replace")


async def probe(path: Path) -> dict:
    """ffprobe metadata: full JSON with streams + format."""
    proc = await _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        timeout=60.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path}: {(proc.stderr or b'').decode(errors='replace')[-500:]}"
        )
    return json.loads(proc.stdout.decode())


async def get_duration(path: Path) -> float:
    meta = await probe(path)
    dur = meta.get("format", {}).get("duration")
    if dur is None:
        for stream in meta.get("streams", []):
            if "duration" in stream:
                dur = stream["duration"]
                break
    if dur is None:
        raise RuntimeError(f"Could not determine duration of {path}")
    return float(dur)


async def get_dimensions(path: Path) -> tuple[int, int]:
    meta = await probe(path)
    for stream in meta.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise RuntimeError(f"No video stream found in {path}")


async def ensure_playable(path: Path) -> None:
    """Guarantee the final MP4 is broadly playable: H.264 video in yuv420p.

    Every encode in the pipeline already pins ``-pix_fmt yuv420p``, so this is a
    safety net — if anything ever slips through as 4:4:4 (or any pixel format
    Windows' Media Player / Films & TV can't decode, which surfaces as
    ``0x80004005 — unsupported encoding settings``), re-encode once in place
    rather than handing the user a file that won't open."""
    meta = await probe(path)
    vstream = next(
        (s for s in meta.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if vstream is None:
        raise RuntimeError(f"No video stream in final output {path}")
    if vstream.get("codec_name") == "h264" and vstream.get("pix_fmt") == "yuv420p":
        return  # already compatible — the common path
    fixed = path.with_name(path.stem + "_compat.mp4")
    await run_ffmpeg(
        ["-i", str(path), "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-b:a", "160k",
         "-movflags", "+faststart", str(fixed)],
        timeout=600.0,
    )
    fixed.replace(path)


# ---- drawtext helpers ----

_FONT_CANDIDATES = {
    "Windows": [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ],
    "Darwin": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "Linux": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
}

_cached_font: str | None = None
_font_resolved = False


def find_font() -> str | None:
    global _cached_font, _font_resolved
    if _font_resolved:
        return _cached_font
    for candidate in _FONT_CANDIDATES.get(platform.system(), _FONT_CANDIDATES["Linux"]):
        if Path(candidate).exists():
            _cached_font = candidate
            break
    _font_resolved = True
    return _cached_font


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("'", "’")  # typographic apostrophe avoids quoting hell
        .replace(":", "\\:")
        .replace("%", "\\%")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def drawtext(text: str, fontsize: int, x: str, y: str, enable: str | None = None,
             alpha: float = 0.6, boxborder: int = 12) -> str:
    font = find_font()
    font_arg = f"fontfile='{escape_filter_path(font)}':" if font else ""
    enable_arg = f":enable='{enable}'" if enable else ""
    return (
        f"drawtext={font_arg}text='{escape_drawtext(text)}':fontsize={fontsize}:"
        f"fontcolor=white:x={x}:y={y}:box=1:boxcolor=black@{alpha}:boxborderw={boxborder}{enable_arg}"
    )


def wrap_lines(text: str, max_chars: int = 55, max_lines: int = 3) -> list[str]:
    lines = textwrap.wrap(text.strip(), width=max_chars)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".") + "…"
    return lines
