"""DemoFlow FastAPI entry point.

Run: uvicorn main:app --reload --port 8000
The vanilla-JS frontend is served at http://localhost:8000 by this app.
"""

import shutil
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.router import api_router
from core.config import REPO_ROOT, settings
from services.ffmpeg import ffmpeg_install_hint, has_ffmpeg
from services.gemini import has_gemini
from services.tts_service import active_engine_name

_ffmpeg_ok = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ffmpeg_ok
    _ffmpeg_ok = has_ffmpeg()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    from core.users import init_db
    init_db()
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found. Install it first: " + ffmpeg_install_hint(), file=sys.stderr)
    if not shutil.which("ffprobe"):
        print("ERROR: ffprobe not found.", file=sys.stderr)
    if not has_gemini():
        print("WARNING: GEMINI_API_KEY not set — instruction parsing and analysis "
              "fall back to deterministic mode. Add your key to .env for full AI quality.")
    print(f"DemoFlow backend ready — output: {settings.output_dir} | headless: {settings.headless}")
    yield


app = FastAPI(title="DemoFlow API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "ffmpeg": _ffmpeg_ok,
        "ffmpeg_install_hint": None if _ffmpeg_ok else ffmpeg_install_hint(),
        "gemini_key": has_gemini(),
        "tts_engine": active_engine_name(),
        "headless": settings.headless,
        "output_dir": str(settings.output_dir),
    }


# Serve the vanilla-JS frontend (mounted last so /api routes win)
_frontend_dir = REPO_ROOT / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)






