# Development guide

## Prerequisites

| Requirement | Install |
|---|---|
| Python 3.11+ | https://python.org |
| FFmpeg + ffprobe (on `PATH`) | `winget install ffmpeg` / `brew install ffmpeg` / `apt install ffmpeg` |
| Gemini API key (optional) | https://aistudio.google.com/apikey |

## Setup & run

```sh
cd backend
pip install -r requirements.txt
playwright install chromium
cd ..
copy .env.example .env        # add GEMINI_API_KEY (optional)
cd backend
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000**. The backend serves `frontend/index.html` and the API from the
same origin.

## Project layout

See [architecture.md](architecture.md) for the annotated component map. Top level:

```
DemoFlow/
├── backend/          FastAPI app, services, models, assets
├── frontend/         index.html — the live UI (vanilla JS)
├── client/           legacy React prototype (design reference, NOT served)
├── docs/             this documentation
├── output/           generated artifacts (gitignored)
└── README.md
```

## How a change flows through the code

- **New animation type:** add to `AnimationType` (`models/flow.py`) → add a branch in
  `build_annotation_filter` (`annotator.py`) → add it to the `ANALYSIS_PROMPT` list and any
  defaulting logic (`vision_analyzer.py`) → add the `<option>` and preview style in
  `frontend/index.html`. (This is exactly how `mouse_pointer` was added.)
- **New auth method:** extend `AuthMethod`/`AuthConfig` (`models/flow.py`) → handle it in
  `_authenticate` (`browser_recorder.py`) → add the UI radio + field and `collectAuth()` key
  in `frontend/index.html`.

## Testing

There is no formal test suite checked in, but the pure functions are easy to exercise
directly. Examples used during development:

**Auth dispatch (mocked Playwright):**
```python
from unittest.mock import AsyncMock, MagicMock
from models.flow import AuthConfig, AuthMethod
from services import browser_recorder as br
# parse_cookie_string("a=1; b=2", url) -> Playwright cookie dicts
# _authenticate(ctx, page, AuthConfig(method=...), url, flow_id, progress)
```

**Annotation filter (renders a real clip):**
```python
from models.flow import FlowStep, AnimationType, BBox
from services.annotator import build_annotation_filter
vf = build_annotation_filter(step, 1280, 800)   # feed to ffmpeg -vf
```

Run a quick syntax check on the backend with:
```sh
python -m py_compile services/*.py models/*.py core/*.py api/*.py main.py
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `NotImplementedError` launching Playwright on Windows | Handled — the recorder runs on a ProactorEventLoop worker thread. If you see it elsewhere, you're spawning a subprocess on a SelectorEventLoop. |
| FFmpeg errors / "ffmpeg missing" banner | FFmpeg or ffprobe not on `PATH`. Install per the table above. |
| Highlight points at the wrong place | Only Vision-bbox steps (non-clicks) rely on the model. Real clicks are anchored to captured coordinates — verify `click_x`/`click_y` are present in `action_timestamps.json`. |
| No mouse cursor in the recording | The live cursor is injected via `_CURSOR_OVERLAY_SCRIPT` (browser_recorder.py). A strict page CSP could block it; check `errors.log`. The `mouse_pointer` animation itself is a passthrough by design. |
| Google sign-in blocked / "JavaScript isn't enabled" | Google blocks automated login. Use the **cookie** method or sign in by hand in **manual mode**. See [authentication.md](authentication.md). |
| No narration audio | Google TTS key missing and `pyttsx3` unavailable → narration is burned in as subtitles. Set `GOOGLE_TTS_API_KEY` for voice. |
| Analysis failed but recording exists | The raw recording is kept; you can retry analysis without re-recording. Errors are in `output/{flow_id}/errors.log`. |

## GCP migration path

| Component | Local | On GCP |
|---|---|---|
| Gemini | `google-generativeai` + key | Vertex AI SDK, same models |
| TTS | REST key / `pyttsx3` | `google-cloud-texttospeech` |
| Recorder | local Chromium | Cloud Run (Playwright image) |
| Rendering | local FFmpeg | Cloud Run Jobs, MP4 → GCS signed URLs |
| Progress bus | in-memory | Pub/Sub or Firestore listeners |
| Storage | `./output/` | GCS bucket, same key layout |
