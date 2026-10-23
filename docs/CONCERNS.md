# Cross-cutting concerns

The system-wide concerns that touch many components: resilience, security, performance,
platform quirks, and known limitations.

## Resilience & fault tolerance

- **Fallback for every AI step.** No Gemini key (or bad output) → deterministic parser,
  timeline-derived steps, template narration. The product never hard-depends on AI.
- **Partial results beat failures.** A failing browser action is logged and skipped, not
  fatal. A recording that was captured is **persisted before analysis**, so analysis can be
  retried without re-recording.
- **TTS degrades gracefully:** Google TTS → pyttsx3 → burned-in subtitles.
- **Errors are captured** per flow in `output/{flow_id}/errors.log` (timestamped tracebacks via
  `core.storage.log_error`) and surfaced to the UI over the SSE progress bus.

## Platform quirks (Windows)

- **Playwright + SelectorEventLoop:** uvicorn `--reload` forces a `SelectorEventLoop` that
  can't spawn subprocesses, killing Chromium with `NotImplementedError`. The recorder runs on a
  dedicated `ProactorEventLoop` in a worker thread; progress is marshalled back to the main loop
  via `_ThreadSafeProgress`.
- **FFmpeg** runs through `subprocess.run` inside `asyncio.to_thread` for the same reason.
- **Headful scrolling:** a fixed emulated `viewport` blocks physical mouse-wheel scrolling in
  headful Chromium. Manual/insert sessions launch with `no_viewport` so the real window scrolls.
- **Case-insensitive filesystem:** doc filenames that differ only in case (e.g.
  `architecture.md` vs `ARCHITECTURE.md`) are the *same* file on Windows.

## Performance

- **Parallel composition** under an `asyncio.Semaphore` (FFmpeg is the bottleneck).
- **Fast intermediate encodes** (`ultrafast`/`veryfast`) for clips that get re-encoded again at
  assembly — quality effort there would be thrown away.
- **Recording pace** is tunable: `RECORD_SLOW_MO` (delay before each action) and
  `RECORD_SETTLE_S` (wait after click/nav). Lower = faster, less watchable.
- **Editor preview** streams `session.mp4` (or a step's own `source_path`) with HTTP Range so
  the `<video>` can seek; the whole-demo preview is a source-aware playlist sequencer.

## Security & privacy

- **App auth:** HMAC-signed httpOnly cookie sessions (stdlib only). Signing key is
  `AUTH_SECRET` or an auto-generated, persisted `output/.auth_secret`. Optional Google
  Sign-In verifies ID tokens server-side.
- **Flow ownership:** flows are owned by the creating user; access is checked
  (`_check_owner`). Legacy ownerless flows remain accessible to any signed-in user.
- **Secrets stay out of artifacts:** manual capture records field **labels, never values**, so
  typed passwords don't reach the logs or `flow.json`.
- **Recorded-site logins** are stored as Playwright `storage_state` per host and reused — treat
  `output/sessions/` as sensitive.
- **Path safety:** client-supplied path segments are validated with `_safe(...)`.
- **CORS** is restricted to `CORS_ORIGINS`; in production the backend serves the frontend from
  the same origin.

## Observability

- `GET /api/health` reports FFmpeg / Gemini / TTS availability (also surfaced as a UI banner).
- SSE progress (`{stage, message, percent}`) gives live status; `errors.log` gives the
  post-mortem.

## Known limitations / trade-offs

- **In-memory progress bus** — progress/cancel state doesn't survive a restart (the recording
  and `flow.json` do).
- **No database** — fine for single-instance; multi-instance needs shared storage (see GCP
  path) since `output/` is local.
- **Inserted-step thumbnail** in the step list reuses the previous frame as a placeholder; the
  timeline filmstrip and rendered video are accurate.
- **Whole-demo editor preview** briefly re-loads when crossing into an inserted step's separate
  file; the final render has no seam.
- **Split** is not covered by the trim/cut Undo stack (like Delete step).
- **Scripted sign-in** to SSO/Google-protected sites is blocked by providers — use manual mode;
  the session is then remembered.

## GCP migration path

| Component | Local | On GCP |
|---|---|---|
| Gemini | `google-generativeai` + key | Vertex AI SDK, same models |
| TTS | REST key / pyttsx3 | `google-cloud-texttospeech` |
| Recorder | local Chromium | Cloud Run (Playwright image) |
| Rendering | local FFmpeg | Cloud Run Jobs, MP4 → GCS signed URLs |
| Progress bus | in-memory | Pub/Sub or Firestore listeners |
| Storage | `./output/` | GCS bucket, same key layout |
