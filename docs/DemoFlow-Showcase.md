# DemoFlow — Product Showcase

**Turn any web app into a polished, narrated demo video — in minutes, not days.**

---

## The problem

Sales, marketing, and customer teams constantly need product demo videos. Today that means
screen-recording by hand, re-recording every mistake, writing a script, recording a voiceover,
and editing it all together — hours of work that goes stale the moment the product changes.

## The solution

DemoFlow drives your **real product in a real browser**, records it, and uses AI to turn that
recording into a finished, narrated MP4 — with on-screen highlights, zooms, and a professional
voiceover. You can record automatically (just type the clicks in plain English) or click
through it yourself.

```
URL + plain-English clicks  →  AI drives & records the browser
   →  AI writes the narration  →  highlights + voiceover added  →  final demo MP4
```

## What it does

- **Records your product** automatically (from plain-English instructions) or manually.
- **Writes the narration** with AI — benefit-focused, in your chosen tone.
- **Polishes on-screen** — animated cursor, click highlights, zooms, title cards.
- **Adds a voiceover** — AI text-to-speech, or record your own voice.
- **Full editor** — rename steps, trim, cut, split, reorder, **insert a forgotten step**, and
  improve any line with one click ("AI Magic").
- **On-brand output** — your logo, brand color, background music, intro & outro.

## Why it matters

| Without DemoFlow | With DemoFlow |
|---|---|
| Hours of manual recording & editing | A finished demo in minutes |
| Re-record the whole thing for one mistake | Edit, trim, or insert just one step |
| Demos go stale as the product evolves | Re-generate against the live product |
| Needs a video editor's skills | Anyone can produce a polished result |

## Under the hood

A lightweight, cloud-ready stack: **Python / FastAPI**, **Playwright** (browser automation),
**Google Gemini** (AI narration & analysis), and **FFmpeg** (video). Every AI step has a
fallback, so it keeps working even without an API key. Designed to lift onto **Google Cloud**
with no architectural change.

## Status

Working application with an end-to-end pipeline and a full editing experience. Roadmap ideas:
seamless multi-clip preview, auto-written narration for inserted steps, and team sharing.

---

