# Workflows

End-to-end flows for users of DemoFlow and for developers working on it.

## User workflows

### 1. Create a demo (automated)
1. Sign in, click **New demo**, enter the **Product URL**.
2. Choose **Automated**, write one instruction per line (plain English).
3. Pick an auth method (**none**, or **manual** to sign in by hand once).
4. Start — the browser opens and runs the steps while recording; watch the live progress.
5. When analysis finishes you land in the **editor** with steps + AI narration.

```
1. Click 'New Project' button
2. Type 'Q4 Demo' in the project name field
3. Click 'Create'
4. Click the 'Analytics' tab
5. Wait 2 seconds
```

### 2. Create a demo (manual)
1. Choose **Manual**, enter the URL, start.
2. A real browser opens — **click through your product yourself** (it scrolls normally). Every
   click / Enter / field change / navigation becomes a step.
3. Press **Stop & Generate** (or close the window). Analysis turns it into narrated steps.

> Tip: for localhost, type the scheme explicitly — `http://localhost:3000` (a bare host gets
> `https://` prepended).

### 3. Edit steps
In the editor you can, per step: rename, edit narration, change the **animation/effect**
(mouse pointer, zoom, crop, highlight, title card), drag the **highlight/zoom region**, set a
**narration start offset**, and record your **own voiceover** instead of TTS.

### 4. Trim & cut
- **Trim Ends** — drag the yellow handles to keep only part of a step.
- **Cut Part** — drag a red box over the filmstrip to remove a span inside a step, then
  **Save edits**.
- **Undo / Redo** covers trims and cuts (persisted per flow).

### 5. Insert a forgotten step
Hover a step and click **add** (or **Insert step at start**) → confirm/adjust the URL → a mini
browser opens → do the one action → **Stop & add step**. It records a self-contained clip and
splices a new step in at that position. (It carries its own footage; see
[ARCHITECTURE.md](ARCHITECTURE.md).)

### 6. Split a step
Scrub the playhead into a step and click **Split** in the timeline toolbar. The step becomes
two: the first half keeps the narration/voice; the second half starts blank for you to narrate
separately. If the playhead isn't positioned, it splits at the midpoint. Both halves share the
footage; trims/cuts are partitioned across the boundary.

### 7. AI Magic (narration)
With a step selected, type a rough line and click **AI Magic** to *polish* it, or leave the box
empty to *generate* one. Respects the **Voice tone** dropdown.

### 8. Generate & render
- **Generate** composes each step (animation overlay + narration) into a clip.
- **Render** assembles the intro card + all clips with crossfades (+ optional logo, music,
  outro) into `final-demo.mp4`. **Download** when done.

## Developer workflows

### Setup & run
```sh
cd backend
pip install -r requirements.txt
playwright install chromium
cd ..
copy .env.example .env        # add GEMINI_API_KEY (optional but recommended)
cd backend
uvicorn main:app --reload --port 8000
```
Open **http://localhost:8000** — the backend serves the frontend.

### Add a new animation type
`AnimationType` (`models/flow.py`) → branch in `build_annotation_filter` (`annotator.py`) →
mention in the analyzer prompt/defaults (`vision_analyzer.py`) → add the `<option>` + preview
in `frontend/index.html`. (This is exactly how `mouse_pointer` was added.)

### Add a new endpoint
Create a router under `api/`, include it in `api/router.py`, delegate work to a `service`,
return Pydantic models. Persist with `save_flow`; publish progress via `progress_bus` for
long-running work. (See `api/insert.py` for a background-task example.)

### Verify a change
No formal suite is checked in — verify by **running the app** and exercising the feature, plus
syntax checks. See [TESTING.md](TESTING.md).

## Endpoint cheat-sheet

| Endpoint | Purpose |
|---|---|
| `POST /api/record` → `GET /api/progress/{id}` (SSE) | start + watch a recording |
| `POST /api/stop/{id}` / `POST /api/continue/{id}` | finish manual recording / resume after login |
| `GET` / `PATCH /api/flow/{id}` | load / save a flow |
| `POST /api/flow/{id}/insert-step` | record + splice a missing step |
| `POST /api/regenerate-script` | AI Magic narration |
| `POST /api/generate` → `POST /api/render` | compose clips → final video |
| `GET /api/output/{id}/final-demo.mp4` | download the result |
| `GET /api/health` | FFmpeg / Gemini / TTS status |
