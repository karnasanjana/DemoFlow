# The pipeline

Five stages. Stages 1–3 run automatically as a background task after `POST /api/record`.
Stages 4–5 are user-triggered (`POST /api/generate`, then `POST /api/render`).

Every AI step has a **deterministic fallback**, so the whole pipeline runs without a Gemini
key — just with template-quality parsing/scripts instead of AI-quality ones.

---

## Stage 1 — Parse instructions → actions

**File:** `services/instruction_parser.py` · **Auto mode only** (manual mode skips this).

- **Primary:** Gemini converts plain-English lines into a JSON array of `ParsedAction`
  (`navigate`, `click`, `type`, `press_key`, `wait`, `scroll`, `hover`, `screenshot`).
- **Fallback:** a regex parser handles the documented numbered format
  (`1. Click "New Project"`, `3. Type "..." in the ... field`, etc.).
- Credential placeholders `{username}` / `{password}` are substituted from the auth config.

---

## Stage 2 — Record the browser session

**File:** `services/browser_recorder.py`

A persistent Chromium context is launched with `record_video`, viewport 1280×800 by default.
The browser is **visible** unless `HEADLESS=true`.

### Auto mode (`record_session`)
1. **Authenticate first** (see [authentication.md](authentication.md)) — cookies/token are
   applied before navigation; credentials log in via the form.
2. Execute each parsed action with `slow_mo=800ms` for a watchable pace.
3. Clicks use a strategy ladder: `role=button` → `role=link` → visible text → CSS selector
   → **Gemini Vision screenshot click** as a last resort.
4. **Real click coordinates are captured** — the center of the clicked element (or the
   Vision click point) is stored on the action timestamp as `click_x`/`click_y`. This is
   what makes the highlight land accurately later (see [animations.md](animations.md)).
5. One failing action never stops the run — it's logged to `errors.log` and skipped.

### Manual mode (`record_manual_session`)
1. Auth runs the same way, then control is handed to the user.
2. An injected capture script reports the user's **clicks (with x/y), Enter presses, field
   changes, and navigations** back to Python as timestamped steps. Field *values* are never
   captured (passwords stay out of the logs — only field labels are recorded).
3. Recording ends when the user clicks **Stop & Generate**, closes the browser, or the
   `max_minutes` limit (default 10) is hit.

**Output of stage 2:** `recording/session.mp4` (transcoded from Playwright's `.webm` to
H.264 for reliable seeking) + `recording/action_timestamps.json`.

---

## Stage 3 — Analyze the recording → steps

**File:** `services/vision_analyzer.py`

1. **Group actions into segments.** Consecutive actions closer than `MIN_STEP_SECONDS`
   (2.5s) become one step (e.g. typing email + password + submit = one "Log in" step). Each
   segment carries the **last real click coordinate** in it, if any.
2. **Extract one frame per segment** (sampled just after the action so its result is visible).
3. **Gemini Vision** receives all frames in one multimodal call and returns, per step: a
   short `name`, a `narration_script`, an `animation_type`, a `bbox` (element to highlight),
   and optional `title_card_text`.
4. **Real clicks override the AI guess.** When a segment has a captured click coordinate,
   the step is forced to `mouse_pointer` anchored to a small box centered on the actual
   click point — instead of trusting the model's frame-based bbox (which used to point at
   the wrong element). Non-click segments keep the AI's framing.
5. **Fallback:** with no key (or after a failed retry), steps are built deterministically
   from the action descriptions.

**Output of stage 3:** `flow.steps` (list of `FlowStep`) + `frames/frame_NNN.jpg`. Status
becomes `ready` and the editor opens.

---

## Stage 4 — Generate per-step clips

**File:** `services/video_composer.py` (+ `annotator.py`, `tts_service.py`) ·
**Endpoint:** `POST /api/generate`

For each step (two in parallel via `Semaphore(2)`):
1. **Annotate** — cut the step's `[start, end]` segment from the recording and overlay its
   animation in one FFmpeg pass (`annotator.annotate_clip`). See [animations.md](animations.md).
2. **Narrate** — synthesize the script to MP3 (`tts_service.synthesize`).
3. **Merge** — combine annotated video + narration. If narration is longer than the clip,
   the last frame freezes to cover it. If TTS fails entirely, the narration is **burned in
   as subtitles** over a silent track.

**Output:** `clips/step_{n}_final.mp4` per step.

---

## Stage 5 — Final assembly

**File:** `services/assembler.py` · **Endpoint:** `POST /api/render`

1. Build a 3-second **intro title card** (flow title + tagline, fade in/out).
2. Chain the intro + every step clip with **`xfade`** (video) and **`acrossfade`** (audio),
   merging progressively in pairs so each intermediate is a valid, debuggable video.
3. Remux with `+faststart` for instant web playback.

**Output:** `output/{flow_id}/final-demo.mp4`, served by
`GET /api/output/{flow_id}/final-demo.mp4`.

---

## Progress & cancellation

Every stage publishes `{stage, message, percent}` to the in-memory progress bus, streamed to
the UI over SSE (`GET /api/progress/{flow_id}`). `POST /api/cancel/{flow_id}` sets a cancel
flag the workers check between steps; `POST /api/stop/{flow_id}` ends a manual recording.
