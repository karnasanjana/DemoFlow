# Technology Stack

DemoFlow is a Python/FastAPI backend that serves a single-file vanilla-JS frontend,
driving a real browser (Playwright) and FFmpeg, with Google Gemini for the AI steps.

## At a glance

| Layer | Technology | Version (min) | Why |
|---|---|---|---|
| Language | Python | 3.11+ | async/await, modern typing (`str | None`) |
| Web framework | FastAPI | 0.115 | async routes, dependency injection, SSE |
| ASGI server | uvicorn[standard] | 0.32 | dev (`--reload`) and prod server |
| Browser automation | Playwright (Chromium) | 1.49 | records a real session via `record_video` |
| AI | google-generativeai (Gemini) | 0.8 | instruction parsing, vision analysis, scripts |
| Video/audio | FFmpeg + ffprobe | system binary | annotation overlays, compositing, assembly |
| TTS (primary) | Google Cloud TTS (REST) | via `httpx` | Neural2 narration voice |
| TTS (fallback) | pyttsx3 | 2.98 | offline voice when no TTS key |
| Data models | pydantic | 2.10 | the data contract (`models/flow.py`) |
| Config | pydantic-settings | 2.6 | `.env` → typed `Settings` |
| HTTP client | httpx | 0.27 | Google TTS + Google token verification |
| Uploads | python-multipart | 0.0.19 | logo / voiceover file uploads |
| Async files | aiofiles | 24.1 | non-blocking file I/O |
| Frontend | Vanilla JS + Tailwind (CDN) | — | one `index.html`, no build step |

> Exact pins live in [`backend/requirements.txt`](../backend/requirements.txt).

## Backend

- **FastAPI app** ([`backend/main.py`](../backend/main.py)) mounts the API router and serves
  `frontend/index.html` as static files from the same origin (so there's no CORS in prod).
- **Pure-async services.** FFmpeg and Playwright are wrapped so blocking work runs off the
  event loop (`asyncio.to_thread`, and a dedicated worker thread on Windows — see
  [CONCERNS.md](CONCERNS.md)).
- **No database.** Each flow's state is a `flow.json` file under `OUTPUT_DIR`; see
  [STRUCTURE.md](STRUCTURE.md).

## Frontend

- A single [`frontend/index.html`](../frontend/index.html) (~2k lines) — markup + Tailwind
  (via CDN with the `forms` plugin) + vanilla JS. No bundler, no framework, no build.
- Three views: **input → progress → editor**, plus a dashboard and auth screen.
- Talks to the backend over `fetch` + an `EventSource` (SSE) progress stream.

## AI & external services

| Service | Used for | Fallback if unavailable |
|---|---|---|
| Gemini (`gemini-2.5-flash`) | parse instructions, analyze recording, write/polish scripts, titles | deterministic parser + template scripts |
| Google Cloud TTS | narration audio | pyttsx3 → burned-in subtitles |
| Google Sign-In | app login (optional) | email/password local accounts |

See [INTEGRATIONS.md](INTEGRATIONS.md) for keys, endpoints, and degradation behavior.

## Tooling assumptions

- **FFmpeg + ffprobe on `PATH`** (`winget install ffmpeg` / `brew install ffmpeg` /
  `apt install ffmpeg`).
- **Chromium installed by Playwright** (`playwright install chromium`).
- A **Gemini API key** is optional but strongly recommended for AI-quality output.

## Legacy

[`client/`](../client/) is a legacy React prototype kept as a **design reference only** — it
is not built or served. The live UI is `frontend/index.html`.
