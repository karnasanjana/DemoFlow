"""Local file storage abstraction (GCS-ready: swap these helpers for bucket keys)."""

import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from core.config import settings
from models.flow import DemoFlow


def flow_dir(flow_id: str) -> Path:
    return settings.output_dir / flow_id


def recording_dir(flow_id: str) -> Path:
    return flow_dir(flow_id) / "recording"


def auth_state_path(url: str) -> Path:
    """Playwright `storage_state` file (cookies + localStorage) keyed by the
    target site's hostname.

    A manual sign-in done once is saved here and replayed on every later run for
    the same site — the standard Playwright auth pattern, more stable than an
    on-disk browser profile.
    """
    host = (urlparse(url).hostname or "default").lower()
    safe = re.sub(r"[^a-z0-9.-]", "_", host) or "default"
    return settings.output_dir / "sessions" / "_state" / f"{safe}.json"


def frames_dir(flow_id: str) -> Path:
    return flow_dir(flow_id) / "frames"


def audio_dir(flow_id: str) -> Path:
    return flow_dir(flow_id) / "audio"


def clips_dir(flow_id: str) -> Path:
    return flow_dir(flow_id) / "clips"


def final_video_path(flow_id: str) -> Path:
    return flow_dir(flow_id) / "final-demo.mp4"


def ensure_flow_dirs(flow_id: str) -> None:
    for d in (flow_dir(flow_id), recording_dir(flow_id),
              frames_dir(flow_id), audio_dir(flow_id), clips_dir(flow_id)):
        d.mkdir(parents=True, exist_ok=True)


def save_flow(flow: DemoFlow) -> None:
    ensure_flow_dirs(flow.id)
    (flow_dir(flow.id) / "flow.json").write_text(flow.model_dump_json(indent=2), encoding="utf-8")


def load_flow(flow_id: str) -> DemoFlow | None:
    path = flow_dir(flow_id) / "flow.json"
    if not path.exists():
        return None
    try:
        return DemoFlow.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_flows(owner_id: str | None = None) -> list[DemoFlow]:
    """All saved flows (optionally only a given owner's), newest first."""
    root = settings.output_dir
    flows: list[DemoFlow] = []
    if not root.exists():
        return flows
    for d in root.iterdir():
        if not d.is_dir() or not (d / "flow.json").exists():
            continue
        flow = load_flow(d.name)
        if flow is None:
            continue
        if owner_id is not None and flow.owner_id != owner_id:
            continue
        flows.append(flow)
    flows.sort(key=lambda f: f.created_at, reverse=True)
    return flows


def delete_flow_dir(flow_id: str) -> bool:
    """Remove a flow's entire output directory. Returns True if it existed."""
    import shutil
    d = flow_dir(flow_id)
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def log_error(flow_id: str, context: str, exc: BaseException | str) -> None:
    """Append timestamp + traceback to output/{flow_id}/errors.log."""
    try:
        ensure_flow_dirs(flow_id)
        if isinstance(exc, BaseException):
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            detail = str(exc)
        line = f"[{datetime.now(timezone.utc).isoformat()}] {context}: {detail}\n"
        with (flow_dir(flow_id) / "errors.log").open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(f"[{flow_id}] ERROR {context}: {exc}")
