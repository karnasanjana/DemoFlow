"""TTS abstraction. Priority:
  1. Google Cloud TTS REST API (GOOGLE_TTS_API_KEY) — neural voices, MP3
  2. edge-tts — Microsoft Edge's free online neural voices. No API key, works in
     every environment (Windows/Linux/Mac/cloud); only needs outbound internet.
     This is the default engine when no Google key is set.
  3. pyttsx3 (fully offline: SAPI on Windows, NSSpeech on mac, espeak on Linux)
  4. None → video_composer burns narration as subtitles instead

All engines share one set of voice keys (see VOICES), so the editor's Male/Female
choice produces a matching male/female voice on whichever engine is active.
"""

import asyncio
import base64
from pathlib import Path

import httpx

from core.config import settings
from core.storage import log_error
from services.ffmpeg import run_ffmpeg


# Selectable narration voices. Each maps to a Google Cloud TTS Neural2 voice, a
# Microsoft Edge neural voice (free, cross-platform, used as the default), and a
# gender hint for the offline pyttsx3 fallback.
VOICES: dict[str, dict[str, str]] = {
    "male":    {"google": "en-US-Neural2-D", "edge": "en-US-GuyNeural",         "gender": "male"},
    "male2":   {"google": "en-US-Neural2-J", "edge": "en-US-ChristopherNeural", "gender": "male"},
    "female":  {"google": "en-US-Neural2-F", "edge": "en-US-AriaNeural",        "gender": "female"},
    "female2": {"google": "en-US-Neural2-G", "edge": "en-US-JennyNeural",       "gender": "female"},
}
DEFAULT_VOICE = "male"


def _resolve_voice(voice: str | None) -> dict[str, str]:
    return VOICES.get(voice or DEFAULT_VOICE, VOICES[DEFAULT_VOICE])


async def _edge_tts(text: str, out_mp3: Path, voice_name: str = "en-US-GuyNeural") -> None:
    """Microsoft Edge online neural TTS — free, no API key, runs on any OS/cloud.
    Needs outbound internet (Edge's read-aloud endpoint)."""
    import edge_tts

    await edge_tts.Communicate(text, voice_name).save(str(out_mp3))


async def _google_cloud_tts(text: str, out_mp3: Path, voice_name: str = "en-US-Neural2-D") -> None:
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={settings.google_tts_api_key}"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": "en-US", "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.0},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"Google TTS API error {response.status_code}: {response.text[:300]}")
    audio_content = response.json().get("audioContent")
    if not audio_content:
        raise RuntimeError("Google TTS returned no audioContent")
    out_mp3.write_bytes(base64.b64decode(audio_content))


# pyttsx3 drives a COM object on Windows (SAPI) — concurrent engines clash,
# so all pyttsx3 synthesis is serialized through this lock.
_pyttsx3_lock = asyncio.Lock()


def _pyttsx3_to_wav_blocking(text: str, wav_path: Path, gender: str = "male") -> None:
    """Runs in a worker thread — pyttsx3 is synchronous and not thread-reentrant,
    so a fresh engine is created per call. On Windows, SAPI is a COM object and
    COM must be initialized per-thread, or init() dies with
    'CoInitialize has not been called'."""
    import sys

    com_initialized = False
    if sys.platform == "win32":
        import comtypes

        comtypes.CoInitialize()
        com_initialized = True
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        # Pick an installed voice matching the requested gender (best effort).
        try:
            voices = engine.getProperty("voices") or []
            want = (gender or "male").lower()
            match = next(
                (v for v in voices
                 if want in (getattr(v, "gender", "") or "").lower()
                 or want in (v.name or "").lower()
                 or (want == "female" and any(n in (v.name or "").lower() for n in ("zira", "hazel", "eva", "susan")))
                 or (want == "male" and any(n in (v.name or "").lower() for n in ("david", "mark", "george")))),
                None,
            )
            if match:
                engine.setProperty("voice", match.id)
        except Exception:
            pass
        engine.save_to_file(text, str(wav_path))
        engine.runAndWait()
        engine.stop()
    finally:
        if com_initialized:
            comtypes.CoUninitialize()


async def _pyttsx3_tts(text: str, out_mp3: Path, gender: str = "male") -> None:
    wav_path = out_mp3.with_suffix(".wav")
    async with _pyttsx3_lock:
        await asyncio.wait_for(asyncio.to_thread(_pyttsx3_to_wav_blocking, text, wav_path, gender), timeout=120.0)
    if not wav_path.exists() or wav_path.stat().st_size == 0:
        raise RuntimeError("pyttsx3 produced no audio file")
    await run_ffmpeg(["-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "4", str(out_mp3)])
    wav_path.unlink(missing_ok=True)


async def synthesize(text: str, output_path: Path, flow_id: str = "global",
                     voice: str | None = None) -> Path | None:
    """Synthesize narration to MP3 in the chosen voice (see VOICES). Returns the
    path, or None if all engines failed."""
    text = text.strip()
    if not text:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    v = _resolve_voice(voice)

    engines = []
    if settings.google_tts_api_key:
        engines.append(("google-cloud-tts", lambda t, o: _google_cloud_tts(t, o, v["google"])))
    # edge-tts: free neural voices that work the same in every environment
    # (local + cloud). Primary engine when no Google key is configured.
    engines.append(("edge-tts", lambda t, o: _edge_tts(t, o, v["edge"])))
    engines.append(("pyttsx3", lambda t, o: _pyttsx3_tts(t, o, v["gender"])))

    for name, engine in engines:
        try:
            await engine(text, output_path)
            if output_path.exists() and output_path.stat().st_size > 0:
                return output_path
            raise RuntimeError("engine produced an empty file")
        except Exception as exc:
            log_error(flow_id, f"tts_service: {name} failed", exc)

    return None


def active_engine_name() -> str:
    return "google-cloud-tts" if settings.google_tts_api_key else "edge-tts"
