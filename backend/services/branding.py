"""Branding assets: background-music catalog, colour conversion, logo paths.

Music tracks are royalty-free beds shipped in assets/music (and any extra .mp3
the user drops there). Logos are uploaded per-flow into the flow directory.
"""

import re
from pathlib import Path

from core.storage import flow_dir

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
MUSIC_DIR = ASSETS_DIR / "music"

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def list_music() -> list[str]:
    """Track names (file stems) available for background music, sorted."""
    if not MUSIC_DIR.exists():
        return []
    return sorted(p.stem for p in MUSIC_DIR.glob("*.mp3"))


def music_path(name: str | None) -> Path | None:
    """Resolve a track name to a file, guarding against path traversal."""
    if not name:
        return None
    safe = Path(name).name  # strip any directory components
    candidate = MUSIC_DIR / f"{safe}.mp3"
    return candidate if candidate.exists() else None


def hex_to_ffmpeg(color: str | None, fallback: str = "0x7F77DD") -> str:
    """'#7F77DD' or '7F77DD' → '0x7F77DD' for FFmpeg drawbox/color. Falls back
    on anything malformed so a bad value never breaks a render."""
    if isinstance(color, str):
        m = _HEX_RE.match(color.strip())
        if m:
            return "0x" + m.group(1).upper()
    return fallback


def logo_path(flow_id: str) -> Path | None:
    """The uploaded logo for a flow, if one exists."""
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = flow_dir(flow_id) / f"logo.{ext}"
        if p.exists():
            return p
    return None
