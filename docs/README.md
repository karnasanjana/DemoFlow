# DemoFlow Documentation

DemoFlow turns a URL + plain-English click instructions (or a manual click-through)
into a polished, narrated demo MP4. It drives a **real browser** with Playwright while
recording, has Gemini watch the recording and write narration, overlays
highlights/cursors/zooms with FFmpeg, adds a TTS voiceover, and assembles the final video.

## Documentation index

### Solution docs (overview set)

| Doc | What's inside |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System overview, components, pipeline, request lifecycle, concurrency |
| [STACK.md](STACK.md) | Technology stack and versions |
| [STRUCTURE.md](STRUCTURE.md) | Repository map + on-disk artifact layout |
| [AGENTS.md](AGENTS.md) | The AI components (Gemini-powered) and their fallbacks |
| [INTEGRATIONS.md](INTEGRATIONS.md) | External services/binaries, config, and degradation |
| [WORKFLOWS.md](WORKFLOWS.md) | User and developer workflows, endpoint cheat-sheet |
| [CONVENTIONS.md](CONVENTIONS.md) | Coding conventions and patterns |
| [CONCERNS.md](CONCERNS.md) | Cross-cutting concerns: resilience, security, performance, limits |
| [TESTING.md](TESTING.md) | How changes are verified |

### Deep dives

| Doc | What's inside |
|---|---|
| [pipeline.md](pipeline.md) | The five stages in detail: parse → record → analyze → generate → render |
| [authentication.md](authentication.md) | Recorded-site sign-in: methods and caveats |
| [branding.md](branding.md) | Brand color, logo watermark, background music, outro card |
| [configuration.md](configuration.md) | Environment variables, settings, on-disk storage layout |
| [development.md](development.md) | Setup, running, testing, troubleshooting, project structure |

## Quick start

```sh
cd backend
pip install -r requirements.txt
playwright install chromium
cd ..
copy .env.example .env        # then add GEMINI_API_KEY (optional but recommended)
cd backend
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend.

Without `GEMINI_API_KEY` the app still runs in a deterministic fallback mode (see
[pipeline.md](pipeline.md)).

## The flow at a glance

```
URL + login + instructions  (or: manual click-through)
  → Gemini parses instructions into Playwright actions      (instruction_parser)
  → Playwright logs in & drives the browser WHILE recording (browser_recorder)
  → Gemini Vision analyzes the recording → steps + scripts  (vision_analyzer)
  → You edit steps/scripts/animations in the editor UI
  → FFmpeg overlays highlight / cursor / zoom / title       (annotator)
  → TTS voiceover merged per step                           (tts_service, video_composer)
  → Intro card + crossfade assembly → final-demo.mp4        (assembler)
```
