# Agents (AI components)

DemoFlow's "AI magic" is a set of focused, single-purpose units powered by **Google Gemini**
(`gemini-2.5-flash` by default). Each one has a **deterministic fallback**, so the whole
product still works without a `GEMINI_API_KEY` — just at template quality instead of
AI quality. All Gemini calls go through one wrapper: [`services/gemini.py`](../backend/services/gemini.py)
(`generate`, `extract_json`, `has_gemini`).

> Working in this repo with an AI coding assistant? See [CONVENTIONS.md](CONVENTIONS.md) for
> the code patterns to follow.

## The agents

| Agent | File | Input → Output | Fallback when no key |
|---|---|---|---|
| **Instruction parser** | `instruction_parser.py` | NL instructions → `ParsedAction[]` (navigate/click/type/press_key/wait/scroll/hover/screenshot) | regex parser for the numbered format |
| **Click vision fallback** | `browser_recorder.py` (`smart_click`) | screenshot + target text → `{x,y}` to click | n/a (only used when selectors fail) |
| **Vision analyzer** | `vision_analyzer.py` | step frames + action timeline → per-step name, narration, bbox, animation | steps built from action descriptions |
| **Script writer** | `script_writer.py` (`rewrite_step_script`) | a step + tone (+ optional draft) → polished/fresh narration | keeps existing text |
| **Title generator** | `script_writer.py` (`generate_title`) | step names + URL → short demo title | hostname-based title |

### Instruction parser
Converts plain-English lines into structured Playwright actions (Gemini → JSON). Credential
placeholders like `{username}`/`{password}` are substituted from the auth config. Auto mode
only; manual mode skips parsing. Fallback: a regex parser that understands the documented
numbered format (`1. Click "New Project"`, `3. Type "..." in the ... field`).

### Click vision fallback (`smart_click`)
During automated recording, clicks try a strategy ladder — `role=button` → `role=link` →
visible text → CSS selector. If all miss, Gemini Vision is shown a screenshot and returns the
pixel coordinates to click. One bad action never stops the run; it's logged and skipped.

### Vision analyzer
The core "watch the recording and write the demo" agent. It groups actions into segments
(consecutive actions <2.5s apart become one step), extracts one frame per segment, and sends
all frames to Gemini in **one multimodal call**. Per step it returns a short `name`, a
benefit-focused `narration_script`, and a `bbox` to highlight. **Real recorded click points
override the model's bbox** when present, so the highlight lands exactly where the click
happened. Fallback: deterministic steps from the action descriptions.

### Script writer — "AI Magic" button
Per-step narration on demand (`POST /api/regenerate-script`). It is **draft-aware**:
- **You typed text** → it *polishes* your line (keeps your meaning and specifics, just says it
  better; won't invent features).
- **The box is empty** → it *writes* a fresh, benefit-focused line for that step.

Honors the **Voice tone** dropdown (professional / casual / executive), capped at ~30 words.

## The automation "agent" (not AI)

Distinct from the Gemini agents: **`browser_recorder.py`** is the agent that actually *drives
the product* — logging in, executing actions, and recording the session. It is deterministic
Playwright automation (with the Vision click as its only AI assist).

## Configuration

| Setting | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | _(empty)_ | enables all agents above; empty → fallbacks |
| `GEMINI_MODEL` | `gemini-2.5-flash` | model id for every Gemini call |

See [INTEGRATIONS.md](INTEGRATIONS.md) for the API details and [CONCERNS.md](CONCERNS.md) for
how invalid model output (bad JSON, empty scripts) is handled.
