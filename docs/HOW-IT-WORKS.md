# How DemoFlow Works — Technical Deep Dive

A complete, plain-English walkthrough of how the application is built: what each library does,
the logic of every stage, and the key decisions. Use this to explain the project end to end.

> Companion docs: [ARCHITECTURE.md](ARCHITECTURE.md) (diagram view), [STACK.md](STACK.md)
> (versions), [STRUCTURE.md](STRUCTURE.md) (file map), [AGENTS.md](AGENTS.md) (the AI parts).

---

## 1. The big picture

DemoFlow takes a **URL + instructions** and produces a **narrated demo MP4**. It does this by
driving a **real browser** while recording, then using **AI to understand the recording and
write narration**, then **FFmpeg to add highlights and a voiceover**, and finally stitching
everything into one video.

```
URL + login + instructions   (or: manual click-through)
  1. Parse instructions  → list of browser actions          (Gemini)
  2. Record the browser  → session.mp4 + action timeline     (Playwright)
  3. Analyze recording   → demo steps + narration scripts    (Gemini Vision)
  4. Generate clips      → per-step: highlight + voiceover    (FFmpeg + TTS)
  5. Assemble            → intro + crossfades → final-demo.mp4 (FFmpeg)
```

Stages 1–3 run automatically in the background right after you start. Stages 4–5 are triggered
from the editor (Generate, then Render). **Every AI step has a non-AI fallback**, so it works
even without an API key.

---

## 2. The tech stack — what each library does and why

| Library | What it does in DemoFlow | Why we chose it |
|---|---|---|
| **FastAPI** | The web server + REST API. Defines all `/api/...` endpoints. | Async, fast, automatic validation via Pydantic, built-in support for streaming (SSE). |
| **uvicorn** | Runs the FastAPI app (the ASGI server). | Standard for FastAPI; `--reload` for dev. |
| **Playwright** | Automates and **records** a real Chromium browser. | Records video natively (`record_video`), reliable selectors, can run headful so users watch. |
| **google-generativeai (Gemini)** | The "brain": parses instructions, watches the recording, writes narration. | One multimodal model handles text *and* images (the recording frames). |
| **FFmpeg / ffprobe** | All video/audio work: cut clips, draw highlights/zoom/text, merge voice, crossfade, watermark, music. | Industry-standard, scriptable, does everything in one toolchain. |
| **Google Cloud TTS** (REST via **httpx**) | Turns narration text into a natural voice. | High-quality Neural2 voices. |
| **pyttsx3** | Offline voice fallback when there's no TTS key. | Works with zero config/keys. |
| **Pydantic** | Defines the data shapes (the "contract") and validates every request. | Type-safe models that serialize straight to/from `flow.json`. |
| **pydantic-settings** | Loads config from `.env` into a typed `Settings` object. | Clean, validated configuration. |
| **python-multipart** | Handles file uploads (logo, custom voiceover). | Required by FastAPI for multipart forms. |
| **aiofiles** | Non-blocking file reads/writes. | Keeps the async event loop responsive. |
| **Vanilla JS + Tailwind (CDN)** | The entire frontend (one `index.html`). | No build step; fast to iterate; easy to serve from the backend. |

**Standard library doing real work too:** `asyncio` (concurrency), `subprocess` (FFmpeg),
`hmac`/`hashlib`/`base64` (cookie-session auth — no JWT library needed).

---

## 3. The data model (the contract)

Everything centers on one Pydantic model in [`models/flow.py`](../backend/models/flow.py):

- **`DemoFlow`** — the whole project: `id`, `owner_id`, `url`, `title`, `steps[]`, `brand`,
  `status`, `raw_recording_path`.
- **`FlowStep`** — one step of the demo: `timestamp_start/end` (its slice of the recording),
  `frame_path`, `narration_script`, `animation_type`, `bbox` (what to highlight), `cuts`
  (trimmed/removed spans), `narration_delay`, `custom_voice_path`, `clip_path`, and
  **`source_path`** (only set for steps inserted later — they carry their own recording).
- **`BrandConfig`** — color, logo, music, outro.
- **`AuthConfig`** — `none` or `manual` sign-in for the recorded site.

There is **no database**. Each flow is saved as `output/{flow_id}/flow.json`
(`core/storage.py` handles all paths and persistence). The frontend mirrors these same field
names in JavaScript.

---

## 4. Stage-by-stage implementation

### Stage 1 — Parse instructions → actions
**File:** [`services/instruction_parser.py`](../backend/services/instruction_parser.py) ·
**Auto mode only.**

- **Logic:** send the user's plain-English lines to Gemini and ask for a JSON array of
  `ParsedAction` objects (`navigate`, `click`, `type`, `wait`, `scroll`, `hover`, …).
- **Fallback:** a regex parser reads the documented numbered format (`1. Click "New Project"`).
- Credential placeholders like `{username}` are substituted from the auth config.

### Stage 2 — Record the browser
**File:** [`services/browser_recorder.py`](../backend/services/browser_recorder.py)

Playwright launches Chromium with `record_video`, navigates, and records while actions run.
Three flavors share the same engine:

- **Automated (`record_session`)** executes each parsed action. Clicks use a **strategy
  ladder**: `role=button` → `role=link` → visible text → CSS selector → **Gemini Vision**
  (screenshot → coordinates) as a last resort. The **real click coordinate is saved**
  (`click_x/click_y`) so the highlight can later land exactly where the click happened. One
  failing action is logged and skipped — a partial recording beats none.
- **Manual (`record_manual_session`)** hands control to the user. An **injected JavaScript
  capture script** reports their clicks (with x/y), Enter presses, field changes, and page
  navigations back to Python as timestamped steps. It records **field labels, never values**,
  so passwords never reach the logs.
- **Insert (`record_insert_session`)** is a short manual session for one forgotten step; it
  writes to its own folder so it never overwrites the main recording.

**Clever bits worth explaining:**
- **Synthetic cursor:** Playwright's video doesn't capture the OS mouse, so we inject an SVG
  cursor + click ring into the page (`_CURSOR_OVERLAY_SCRIPT`) — the mouse is visible in the
  video.
- **Stealth:** a small init script hides `navigator.webdriver` so identity providers don't flag
  the session as a bot.
- **Remembered logins:** a manual sign-in is saved as Playwright `storage_state` per website,
  and replayed on later runs — no need to log in again.
- **`no_viewport` for manual mode:** a fixed emulated viewport blocks physical mouse-wheel
  scrolling in a headful window, so manual/insert sessions use the **real OS window** and scroll
  normally.
- **Windows event-loop workaround:** uvicorn `--reload` forces an event loop that can't spawn
  subprocesses (Chromium dies with `NotImplementedError`). We run the recorder on a dedicated
  `ProactorEventLoop` in a **worker thread** and marshal progress back to the main loop.

**Output:** `recording/session.mp4` (transcoded to H.264 for reliable seeking) + the action
timeline.

### Stage 3 — Analyze the recording → steps
**File:** [`services/vision_analyzer.py`](../backend/services/vision_analyzer.py)

- **Group actions into segments:** consecutive actions less than 2.5s apart become one step
  (typing email + password + submit = one "Log in" step).
- **Extract one frame per segment** (sampled just after the action so its result is visible).
- **One multimodal Gemini call:** all frames are sent together; Gemini returns, per step, a
  short `name`, a benefit-focused `narration_script`, and a `bbox` (what to highlight).
- **Ground truth wins:** when we have the real recorded click point, we anchor the highlight to
  it instead of trusting the model's guess.
- **Fallback:** with no key, steps are built from the action descriptions.

### Stage 4 — Generate per-step clips
**Files:** [`services/video_composer.py`](../backend/services/video_composer.py) +
[`annotator.py`](../backend/services/annotator.py) + [`tts_service.py`](../backend/services/tts_service.py)

For each step (several in parallel, capped by an `asyncio.Semaphore`):
1. **Annotate:** cut the step's `[start, end]` from its source video and overlay its animation
   in **one FFmpeg pass** — `mouse_pointer` (the recorded cursor/ring), `zoom`, `crop`,
   `highlight` (pulsing brand-color box), or `title_card`. The animation filter is built as an
   FFmpeg `-vf` string in `build_annotation_filter`.
2. **Narrate:** synthesize the script to MP3 — **Google TTS → pyttsx3 → burned-in subtitles**.
3. **Merge:** combine the annotated video + voice. If the voice is longer than the clip, the
   last frame freezes to cover it.

Inserted steps simply read from their own `source_path` here — everything else is identical.

### Stage 5 — Assemble the final video
**File:** [`services/assembler.py`](../backend/services/assembler.py)

1. Build a **3-second intro title card** (flow title + accent bar, fade in/out).
2. Chain intro + every step clip with **`xfade`** (video) + **`acrossfade`** (audio), merging in
   pairs so each intermediate is a valid video.
3. **Finalize:** overlay the **logo watermark**, mix in **background music** that automatically
   ducks under the narration, and remux with `+faststart` for instant web playback.

**Output:** `output/{flow_id}/final-demo.mp4`.

---

## 5. The editor (frontend)

One file: [`frontend/index.html`](../frontend/index.html) — vanilla JS, three views
(input → progress → editor). Notable implementations:

- **Live progress** via **Server-Sent Events** (`EventSource` → `/api/progress/{id}`): the
  backend pushes `{stage, message, percent}` as the pipeline runs.
- **Per-step preview source:** the preview `<video>` plays from the shared `session.mp4` for
  normal steps, or a step's **own file** for inserted steps — chosen by `stepVideoUrl(s)`.
- **Whole-demo preview** is a **source-aware playlist sequencer**: it plays each step's kept
  intervals in order, swapping the video source when it crosses into an inserted step.
- **Trim / Cut:** stored as `cuts` (spans to remove) on each step; the timeline maps pixels to
  source time and applies them in the FFmpeg filter at generate time.
- **Split:** divides a step in two at the playhead; cuts are partitioned across the boundary;
  the first half keeps the narration, the second starts blank.
- **Insert step:** records a mini-session and splices a new step (with its own `source_path`)
  into the list.
- **AI Magic:** sends your live draft to `/api/regenerate-script`; if you typed something it
  **polishes** it, if empty it **generates** — honoring the tone dropdown.
- **Toasts, not alerts:** routine feedback uses an on-screen toast because Chrome can suppress
  `alert`/`confirm` after repeated use.

---

## 6. Authentication (the app itself)

Separate from the recorded site's sign-in. The app supports **email/password** accounts and
optional **Google Sign-In**, using **HMAC-signed httpOnly cookie sessions** built with the
standard library only (`core/auth.py` — no JWT dependency). Google ID tokens are verified
server-side against Google's tokeninfo endpoint. Flows are owned by their creator.

---

## 7. Key technical decisions (and the "why")

- **Real browser, real recording** (not a synthetic re-creation) → the demo shows the actual
  product behaving exactly as it does for users.
- **AI with deterministic fallbacks everywhere** → the product never hard-fails because an API
  key is missing or a model returns junk.
- **Files instead of a database** → simple, debuggable, and easy to retry (the recording is
  saved before analysis, so analysis can re-run without re-recording).
- **Thin API, fat services** → endpoints stay tiny; all logic is in testable service functions.
- **One FFmpeg pass per effect** → frame-accurate and fast.
- **Per-step `source_path`** → lets a single inserted clip live alongside the shared recording
  without re-architecting anything.
- **Cloud-ready by design** → each external piece (Gemini, TTS, recorder, rendering, storage)
  maps cleanly onto Google Cloud equivalents (see [CONCERNS.md](CONCERNS.md)).

---

## 8. Running it

```sh
cd backend
pip install -r requirements.txt
playwright install chromium
cd ..
copy .env.example .env        # add GEMINI_API_KEY (optional but recommended)
cd backend
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend from the same origin. Full
setup and troubleshooting: [development.md](development.md).
