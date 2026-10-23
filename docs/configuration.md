# Configuration

## Environment variables

Settings load from `.env` (repo root or `backend/`) via pydantic-settings
(`core/config.py`). Copy `.env.example` to `.env` and fill in:

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | _(empty)_ | Enables AI instruction parsing, vision analysis, and script writing. Without it, deterministic fallbacks are used. Get one at https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model id used for all calls. |
| `GOOGLE_TTS_API_KEY` | _(empty)_ | Enables Google Cloud TTS (Neural2 voice). Without it, TTS falls back to offline `pyttsx3`, then to burned-in subtitles. |
| `OUTPUT_DIR` | `./output` | Where recordings, frames, clips, sessions, and final videos are written. Relative paths resolve from the repo root. |
| `PORT` | `8000` | uvicorn port. |
| `HEADLESS` | `false` | When `false`, the recording browser is **visible** so you can watch and intervene (captcha / manual login). Set `true` for unattended runs. |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000` | Comma-separated allowed origins. |

## On-disk storage layout

No database — each flow's state is files under `OUTPUT_DIR`. Helpers live in
`core/storage.py`.

```
output/
├── sessions/
│   └── {flow_id}/              persistent Chromium profile (logins survive re-runs)
└── {flow_id}/
    ├── flow.json               serialized DemoFlow (the source of truth for a flow)
    ├── errors.log              timestamped errors + tracebacks for this flow
    ├── auth_result.png         screenshot after a credentials login (debug)
    ├── recording/
    │   ├── session_recording.webm   raw Playwright capture
    │   ├── session.mp4              transcoded H.264 (used downstream)
    │   └── action_timestamps.json   [{action_index, description, timestamp_seconds, click_x?, click_y?}]
    ├── frames/
    │   └── frame_NNN.jpg        one representative frame per step
    ├── audio/
    │   └── step_N.mp3           per-step TTS narration
    ├── clips/
    │   ├── step_N_annotated.mp4 segment + animation overlay
    │   ├── step_N_final.mp4     annotated + narration merged
    │   └── intro.mp4            generated title card
    └── final-demo.mp4           the deliverable
```

Everything under `output/` is gitignored (except `output/.gitkeep`).

## Static assets

- [`backend/assets/music/`](../backend/assets/music/) — royalty-free background-music beds
  (`ambient.mp3`, `focus.mp3`). Drop more `.mp3` files here to offer them in the UI. See
  [branding.md](branding.md).

The live mouse cursor shown in recordings is injected as inline SVG at capture time (no image
asset) — see [animations.md](animations.md).

## Fonts

`drawtext` overlays pick the first available system font (`ffmpeg.py` `_FONT_CANDIDATES`):
Segoe UI/Arial/Calibri on Windows, Arial/Helvetica on macOS, DejaVu/Liberation on Linux. If
none is found, text overlays are skipped rather than failing the render.
