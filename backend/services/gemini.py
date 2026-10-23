"""Thin Gemini wrapper + JSON extraction shared by all AI services.

GCP MIGRATION: swap to Vertex AI (google-cloud-aiplatform) — identical
generate_content call shapes, enterprise quota.
"""

import json
import re
from typing import Any

import google.generativeai as genai

from core.config import settings


def has_gemini() -> bool:
    return bool(settings.gemini_api_key)


async def generate(parts: list | str) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    response = await model.generate_content_async(parts)
    return response.text


def extract_json(raw: str) -> Any:
    """Pull a JSON value out of a model response that may include fences/prose."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        raise ValueError("No JSON found in model response")
    close = "]" if text[start] == "[" else "}"
    end = text.rfind(close)
    if end <= start:
        raise ValueError("Unbalanced JSON in model response")
    return json.loads(text[start : end + 1])
