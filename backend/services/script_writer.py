"""Gemini: rewrite narration scripts + generate the flow title."""

from urllib.parse import urlparse

from models.flow import FlowStep
from services.gemini import generate, has_gemini


def _clean(text: str) -> str:
    return " ".join(text.strip().strip("\"'`").split())


async def rewrite_step_script(product_name: str, step: FlowStep, tone: str = "professional",
                              draft: str | None = None) -> str:
    """AI Magic for one step's narration.

    `draft` is the user's live text from the editor. When it has real content the
    model POLISHES it (keeps the user's meaning, just says it better); when it's
    empty the model WRITES a fresh line from the step. `draft=None` falls back to
    the step's saved script (old behavior)."""
    current = step.narration_script if draft is None else draft
    current = (current or "").strip()

    if current:
        task = (
            "Improve this voiceover line for a product demo. Keep the speaker's meaning and "
            "any specifics they mention — just make it clearer, smoother, and more compelling. "
            "Do not invent features that aren't implied."
        )
    else:
        task = "Write a compelling, benefit-focused voiceover line for this product demo step."

    prompt = f"""You are a world-class sales demo narrator. {task}
Product: {product_name}
Step name: {step.name}
Draft to improve: {current or "(none — write from scratch)"}
Tone: {tone} (professional/casual/executive)
Max words: 30
Return ONLY the voiceover text. No quotes, no notes, no explanation."""
    script = _clean(await generate(prompt))
    if not script:
        raise RuntimeError("Model returned an empty script")
    words = script.split()
    return " ".join(words[:30]) + "…" if len(words) > 34 else script


async def generate_title(steps: list[FlowStep], url: str) -> str:
    """Short demo title — Gemini when available, hostname otherwise."""
    host = (urlparse(url).hostname or "Product").removeprefix("www.")
    if not has_gemini() or not steps:
        return f"{host.split('.')[0].title()} Demo"
    try:
        names = ", ".join(s.name for s in steps[:6])
        title = _clean(await generate(
            f"Product demo covering: {names}. Website: {url}.\n"
            "Write a short, punchy demo video title (max 6 words). Return ONLY the title."
        ))
        return title[:60] or f"{host.split('.')[0].title()} Demo"
    except Exception:
        return f"{host.split('.')[0].title()} Demo"
