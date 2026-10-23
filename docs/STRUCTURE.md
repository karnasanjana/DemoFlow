# Project Structure

A map of the repository and what each part is responsible for. For how the pieces interact at
runtime, see [ARCHITECTURE.md](ARCHITECTURE.md).

```
DemoFlow/
├── backend/                     FastAPI app, services, models, assets
│   ├── main.py                  app entry: CORS, /api/health, mounts router + static frontend
│   ├── requirements.txt         Python dependencies
│   ├── api/                     HTTP layer (thin; delegates to services)
│   │   ├── router.py            includes every router below
│   │   ├── auth.py              signup / login / google / logout / me / config
│   │   ├── record.py            POST /api/record, /api/stop, /api/continue + run_pipeline
│   │   ├── flow.py              GET/PATCH flow, list, delete, serve frames/recording/video/logo/voice
│   │   ├── insert.py            POST /api/flow/{id}/insert-step (record + splice a missing step)
│   │   ├── generate.py          POST /api/generate (annotate + narrate + compose all steps)
│   │   ├── render.py            POST /api/render (intro + crossfade assembly), /api/cancel
│   │   ├── script.py            POST /api/regenerate-script (AI Magic narration)
│   │   └── progress.py          GET /api/progress (SSE), POST /api/cancel
│   ├── services/                the actual work
│   │   ├── instruction_parser.py  NL instructions → Playwright actions (Gemini + regex fallback)
│   │   ├── browser_recorder.py    login + drive + record (auto, manual, and insert sessions)
│   │   ├── vision_analyzer.py     recording + action timeline → steps + narration
│   │   ├── insert_step.py         a single insert recording → one FlowStep (own source_path)
│   │   ├── annotator.py           per-step FFmpeg animation overlay
│   │   ├── tts_service.py         narration synthesis (Google TTS → pyttsx3 → subtitles)
│   │   ├── video_composer.py      annotated clip + narration → per-step clip
│   │   ├── assembler.py           intro card + crossfade concat → final-demo.mp4
│   │   ├── script_writer.py       title + per-step narration (generate / polish)
│   │   ├── branding.py            brand color, music list, logo helpers
│   │   ├── gemini.py              Gemini client wrapper + JSON extraction
│   │   └── ffmpeg.py              async ffmpeg/ffprobe helpers + drawtext
│   ├── core/
│   │   ├── config.py            pydantic-settings (.env → Settings)
│   │   ├── storage.py           on-disk layout helpers + flow.json persistence
│   │   ├── progress.py          in-memory SSE progress bus + cancel/stop/continue flags
│   │   ├── auth.py              HMAC cookie sessions + current_user dependency + Google verify
│   │   └── users.py            user records (signup/lookup)
│   ├── models/flow.py           all Pydantic models (the data contract)
│   └── assets/music/            royalty-free background-music beds
├── frontend/
│   ├── index.html               the live UI — vanilla JS, 3 views (input/progress/editor)
│   └── *.jpg / assets           logos and static images
├── client/                      legacy React prototype (design reference, NOT served)
├── docs/                        this documentation
├── output/                      generated artifacts (gitignored)
└── README.md
```

## On-disk artifacts (`output/`)

No database — each flow is files under `OUTPUT_DIR`. Helpers: [`core/storage.py`](../backend/core/storage.py).

```
output/
├── .auth_secret                 auto-generated cookie-signing key (dev)
├── sessions/_state/{host}.json  Playwright storage_state — remembered logins per site
└── {flow_id}/
    ├── flow.json                serialized DemoFlow (source of truth for a flow)
    ├── errors.log               timestamped errors + tracebacks
    ├── recording/
    │   ├── session.mp4          transcoded H.264 recording (main timeline)
    │   ├── session_recording.webm  raw Playwright capture
    │   └── inserts/{id}/insert.mp4  self-contained clip for an inserted step
    ├── frames/
    │   ├── frame_NNN.jpg         one representative frame per analyzed step
    │   └── insert_{id}.jpg       representative frame for an inserted step
    ├── audio/                    per-step TTS + user voiceovers
    ├── clips/                    step_N_annotated.mp4, step_N_final.mp4, intro.mp4
    └── final-demo.mp4            the deliverable
```

## The data contract

[`backend/models/flow.py`](../backend/models/flow.py) defines every shape (`DemoFlow`,
`FlowStep`, `BrandConfig`, `AuthConfig`, request bodies). **The frontend mirrors these shapes
in `index.html` — keep the two in sync** (see [CONVENTIONS.md](CONVENTIONS.md)).

Key `FlowStep` fields: `timestamp_start/end` (span into its source), `frame_path`,
`narration_script`, `animation_type`, `bbox`, `cuts`, `custom_voice_path`, `narration_delay`,
`clip_path`, and `source_path` (set only for editor-inserted steps that carry their own
recording).
