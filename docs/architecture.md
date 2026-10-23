# Architecture

DemoFlow turns a URL + plain-English instructions (or a manual click-through) into a polished,
narrated demo MP4. It drives a **real browser** while recording, has Gemini watch the
recording and write narration, overlays highlights/cursors/zooms with FFmpeg, adds a TTS
voiceover, and assembles the final video.

## Stack (summary)

Python 3.11 · FastAPI/uvicorn · Playwright (Chromium) · Google Gemini · FFmpeg/ffprobe ·
Google Cloud TTS (→ pyttsx3 → subtitles) · vanilla-JS single-file frontend. Full details in
[STACK.md](STACK.md); file map in [STRUCTURE.md](STRUCTURE.md).

## Component map

```
backend/
├── main.py            FastAPI app, /api/health, static frontend mount
├── api/               HTTP layer (thin; delegates to services)
│   ├── auth · record · flow · insert · generate · render · script · progress
├── services/          the actual work
│   ├── instruction_parser   NL → Playwright actions
│   ├── browser_recorder     login + drive + record (auto / manual / insert)
│   ├── vision_analyzer      recording + timeline → steps + narration
│   ├── insert_step          one insert recording → one FlowStep
│   ├── annotator            per-step FFmpeg animation overlay
│   ├── tts_service          narration synthesis with fallbacks
│   ├── video_composer       annotated clip + narration → step clip
│   ├── assembler            intro + crossfade concat → final-demo.mp4
│   ├── script_writer        title + narration generate/polish (Gemini)
│   ├── branding · gemini · ffmpeg
├── core/              config · storage · progress (SSE bus) · auth · users
└── models/flow.py     all Pydantic models (the data contract)
```

## The pipeline

```
URL + login + instructions   (or: manual click-through)
  → Gemini parses instructions into Playwright actions       (instruction_parser)
  → Playwright logs in & drives the browser WHILE recording  (browser_recorder)
  → Gemini Vision analyzes the recording → steps + scripts   (vision_analyzer)
  → You edit steps / scripts / animations in the editor
  → FFmpeg overlays highlight / cursor / zoom / title         (annotator)
  → TTS voiceover merged per step                             (tts_service, video_composer)
  → Intro card + crossfade assembly → final-demo.mp4          (assembler)
```

Stages 1–3 run automatically as a background task after `POST /api/record`. Stages 4–5 are
user-triggered (`/api/generate`, then `/api/render`). **Every AI step has a deterministic
fallback**, so the whole pipeline runs without a Gemini key. The per-stage detail lives in the
existing [pipeline.md](pipeline.md).

## Request lifecycle

1. **`POST /api/record`** validates input, creates a `flow_id`, persists an initial
   `flow.json`, and schedules `run_pipeline` as a background task. Returns `202 {flow_id}`.
2. The browser opens; **parse → record → analyze** run in the background. The browser is
   **visible by default** so the user can watch and intervene (captcha / manual login).
3. The frontend subscribes to **`GET /api/progress/{flow_id}`** (SSE) for live updates.
4. When analysis finishes, status becomes `ready` and the editor loads the steps. The user
   edits names/scripts/animations/trims and saves with **`PATCH /api/flow`**.
5. **`POST /api/generate`** composes every step (annotation + narration); **`POST /api/render`**
   assembles intro + steps into `final-demo.mp4`, served by
   `GET /api/output/{flow_id}/final-demo.mp4`.

## Recording modes

| Mode | Driver | Captured as steps |
|---|---|---|
| **Automated** | Playwright executes parsed instructions | each action's timestamp + click point |
| **Manual** | the user clicks through the visible browser | an injected script reports clicks (x/y), Enter, field changes (labels only), navigations |
| **Insert** | manual mini-session for one forgotten step | spliced in at a chosen position with its own recording |

## Per-step source model

Normally every step is a span (`timestamp_start`→`timestamp_end`) of the single
`recording/session.mp4`. A step **inserted in the editor** instead carries its own little
recording via `FlowStep.source_path`; its timestamps are relative to that file (0 → duration).
Composition resolves `step.source_path or session.mp4` per step, so annotation, narration, and
assembly are unaffected and the final render concatenates all step clips in order. This is what
lets you record just one missing step and splice it into the middle without re-recording.

## State & persistence

There is **no database**. Each flow's state lives in `output/{flow_id}/flow.json` (a serialized
`DemoFlow`). The progress bus is **in-memory** (`core/progress.py`), so progress/cancel state
does not survive a restart — but the recording and `flow.json` do, so analysis or generation
can be retried without re-recording. See [STRUCTURE.md](STRUCTURE.md) for the on-disk layout
and [configuration.md](configuration.md) for settings.

## App authentication

Separate from the *recorded site's* sign-in: the DemoFlow app itself supports email/password
accounts and optional Google Sign-In, using **HMAC-signed cookie sessions** (stdlib only, no
JWT lib) — `core/auth.py`. Flows are owned by the creating user; legacy ownerless flows stay
accessible. See [INTEGRATIONS.md](INTEGRATIONS.md).

## Concurrency & Windows notes

- **Playwright on Windows:** uvicorn `--reload` forces a `SelectorEventLoop`, which cannot
  spawn subprocesses (Chromium driver dies with `NotImplementedError`). The recorder sidesteps
  this by running on a dedicated `ProactorEventLoop` in a worker thread; progress calls are
  marshalled back to the main loop via `_ThreadSafeProgress`.
- **FFmpeg** runs via `subprocess.run` inside `asyncio.to_thread` for the same reason.
- **Step composition** runs steps in parallel under an `asyncio.Semaphore`.
- **Manual/insert recording** launches with `no_viewport` so the real OS window scrolls
  naturally (a fixed emulated viewport blocks physical mouse-wheel scrolling in headful
  Chromium). See [CONCERNS.md](CONCERNS.md).
