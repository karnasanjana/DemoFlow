# Conventions

Patterns to follow when working in this codebase. The goal is that new code reads like the
code already here.

## Python

- **Target 3.11+.** Use modern typing: `str | None`, `list[dict]`, no `Optional`/`List` imports.
- **Async everywhere in the request path.** Never block the event loop — wrap blocking work
  (`subprocess`, file I/O) in `asyncio.to_thread` (see `services/ffmpeg.py`). On Windows,
  Playwright runs on a dedicated `ProactorEventLoop` worker thread (see [CONCERNS.md](CONCERNS.md)).
- **Thin API, fat services.** Routers in `api/` validate, call a `service`, and return models.
  Real work lives in `services/`. Don't put FFmpeg/Playwright/Gemini logic in routers.
- **Pydantic is the contract.** All shapes live in `models/flow.py`. Add fields as
  **additive + defaulted** so old `flow.json` files keep loading (e.g. `source_path: str | None = None`).
- **Private helpers are `_prefixed`** and may be imported across services when genuinely shared
  (e.g. `vision_analyzer._best_description`). Public service functions are the verbs
  (`record_session`, `compose_all`, `assemble`).
- **Every AI call has a deterministic fallback.** Guard with `has_gemini()`; on bad/empty model
  output, retry once stricter, then fall back. Never let one Gemini hiccup break the pipeline.
- **One bad action never crashes a run.** Catch, `log_error(flow_id, context, exc)`, and
  continue — a partial recording beats none.

## Errors & logging

- Use `core.storage.log_error(flow_id, context, exc)` — it appends a timestamped traceback to
  `output/{flow_id}/errors.log` and prints to console.
- Surface user-facing problems over the progress bus
  (`progress_bus.publish_error(flow_id, message)`), not as raw 500s where avoidable.
- Prefer a clear message over an empty one (e.g. `str(exc).strip() or type(exc).__name__`).

## Naming & files

- Files: `snake_case.py`, one responsibility each. Add new endpoints as a small router under
  `api/` and include it in `api/router.py`.
- Storage paths go through `core/storage.py` helpers (`flow_dir`, `recording_dir`,
  `frames_dir`, `clips_dir`, …) — don't hand-build paths. This keeps the GCS migration a
  one-file change.
- Sanitize any path segment from the client with `_safe(...)` before using it.

## Frontend (`frontend/index.html`)

- **Vanilla JS, no build, no framework.** One file. Tailwind via CDN (with the `forms`
  plugin — note that plugin sets `appearance:none` on inputs, so radios are styled with CSS).
- **Mirror the Pydantic models** when reading/writing flow data; keep field names identical to
  `models/flow.py`.
- **No blocking dialogs for routine feedback.** Chrome can suppress `alert`/`confirm` after
  repeated use; use the `toast(message)` helper instead. Reserve `confirm` for truly
  destructive, rare actions.
- Render helpers are small and named for what they paint: `renderStepList`, `renderPreview`,
  `renderPanel`, `renderTimelineFull`, `refreshFilmstrip`.
- Debounce persistence with `queueSave()`; it `PATCH`es the whole steps array.

## Configuration

- All settings flow through `core/config.py` (`settings.*`), loaded from `.env`. Don't read
  `os.environ` directly. Document every new variable in [configuration.md](configuration.md)
  and [INTEGRATIONS.md](INTEGRATIONS.md).

## Documentation

- Keep docs grounded in the actual code; link to source with relative paths
  (e.g. `[annotator.py](../backend/services/annotator.py)`).
- When you add a feature that changes behavior, update the relevant doc(s) in the same change.

## Git

- Commit/push only when asked. Branch off `main` first if needed. Write present-tense,
  scoped commit messages describing the behavior change.
