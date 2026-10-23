# Integrations

External services and binaries DemoFlow depends on, how they're configured, and how the system
degrades when each is unavailable. Settings come from `.env` via `core/config.py`
(see [configuration.md](configuration.md)).

## Google Gemini (AI)

- **Library:** `google-generativeai`. **Wrapper:** [`services/gemini.py`](../backend/services/gemini.py).
- **Used by:** instruction parser, vision analyzer, script writer, title generator, click
  vision fallback (see [AGENTS.md](AGENTS.md)).
- **Config:** `GEMINI_API_KEY` (required to enable AI), `GEMINI_MODEL` (default
  `gemini-2.5-flash`).
- **Degradation:** no key → deterministic fallbacks everywhere; invalid JSON → one stricter
  retry, then fallback.
- **GCP path:** swap for the Vertex AI SDK (same models).

## Playwright + Chromium (browser automation)

- **Library:** `playwright` (async API). **Code:** [`services/browser_recorder.py`](../backend/services/browser_recorder.py).
- **Setup:** `playwright install chromium`.
- **How it's used:** a non-persistent Chromium context with `record_video` captures the
  session while actions run. A synthetic cursor + click ring is injected (video capture
  doesn't include the OS cursor), and a stealth init script masks `navigator.webdriver`.
- **Sign-in reuse:** a manual login is saved as Playwright `storage_state` keyed by site host
  (`output/sessions/_state/{host}.json`) and replayed on later runs.
- **Scrolling:** manual/insert sessions launch with `no_viewport` so the page uses the real OS
  window and scrolls with the wheel/trackpad (a fixed emulated viewport blocks physical
  scrolling in headful Chromium).
- **GCP path:** run in a Cloud Run container built from the Playwright image.

## FFmpeg + ffprobe (video/audio)

- **Binaries on `PATH`.** **Wrapper:** [`services/ffmpeg.py`](../backend/services/ffmpeg.py)
  (async via `asyncio.to_thread`).
- **Used by:** `annotator` (cut + overlay), `video_composer` (merge narration),
  `assembler` (intro/outro cards, crossfade concat, logo watermark, music duck, `+faststart`),
  and frame/transcode steps in the recorder.
- **Degradation:** missing FFmpeg → startup error in the terminal + a banner in the UI
  (`/api/health` reports status). Text overlays skip gracefully if no system font is found.

## Text-to-speech

A three-tier fallback chain (`services/tts_service.py`):

1. **Google Cloud TTS (REST)** — Neural2 voice, called via `httpx`. Config: `GOOGLE_TTS_API_KEY`.
2. **pyttsx3** — offline system voice when no TTS key.
3. **Burned-in subtitles** — if both fail, the narration is drawn on a silent track so the
   step still "speaks."

- **GCP path:** the `google-cloud-texttospeech` client.

## Google Sign-In (app login)

- **Optional.** Verifies Google ID tokens against Google's `tokeninfo` endpoint (`httpx`) when
  `GOOGLE_CLIENT_ID` is set — `core/auth.py` (`verify_google_credential`).
- The frontend shows the Google button only when `GET /api/auth/config` reports a client id.
- Without it, users sign up / log in with **email + password** (local accounts in
  `core/users.py`). Sessions are **HMAC-signed httpOnly cookies** (stdlib only, no JWT lib);
  the signing key is `AUTH_SECRET` or an auto-generated `output/.auth_secret`.

## System fonts (text overlays)

`drawtext` picks the first available system font (`ffmpeg.py` `_FONT_CANDIDATES`): Segoe
UI/Arial/Calibri (Windows), Arial/Helvetica (macOS), DejaVu/Liberation (Linux). None found →
overlays are skipped rather than failing the render.

## Bundled assets

- [`backend/assets/music/`](../backend/assets/music/) — royalty-free background-music beds.
  Drop more `.mp3` files there and they appear in the UI. `GET /api/music` lists them. See
  [branding.md](branding.md).

## Configuration summary

| Variable | Enables |
|---|---|
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Gemini agents |
| `GOOGLE_TTS_API_KEY` | Google Cloud TTS voice |
| `GOOGLE_CLIENT_ID` | Google Sign-In button |
| `AUTH_SECRET` | stable cookie-session signing across restarts |
| `HEADLESS` | hide/show the recording browser |
| `OUTPUT_DIR`, `PORT`, `CORS_ORIGINS`, `RECORD_SLOW_MO`, `RECORD_SETTLE_S` | runtime tuning |
