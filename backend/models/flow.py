"""Pydantic models for the record-session flow.

The frontend mirrors these shapes in frontend/index.html — keep in sync.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class AuthMethod(str, Enum):
    none = "none"                # no sign-in required
    manual = "manual"            # user signs in by hand once; the session is remembered


class AuthConfig(BaseModel):
    method: AuthMethod = AuthMethod.none
    # Optional page to open for signing in. Defaults to the target URL.
    login_url: str | None = None


class RecordMode(str, Enum):
    auto = "auto"      # instructions parsed and executed by Playwright
    manual = "manual"  # the user clicks through the opened browser themselves


class RecordRequest(BaseModel):
    url: str
    instructions: str = ""  # required for auto mode, unused in manual mode
    mode: RecordMode = RecordMode.auto
    auth: AuthConfig = Field(default_factory=AuthConfig)
    viewport_width: int = 1280
    viewport_height: int = 800


class ParsedAction(BaseModel):
    type: str                      # navigate, click, type, wait, scroll, hover, screenshot
    selector: str | None = None
    text: str | None = None        # for type actions
    url: str | None = None         # for navigate actions
    duration: float | None = None  # for wait actions
    description: str


class AnimationType(str, Enum):
    # --- Curated set offered in the editor (anchored to the cursor/click point) ---
    mouse_pointer = "mouse_pointer"  # live recorded cursor + brand-color click ripple
    zoom = "zoom"                    # smooth zoom punch into the click point
    crop = "crop"                    # static crop to selected region scaled to full frame
    highlight = "highlight"          # pulsing brand-color border box around the target
    title_card = "title_card"        # large centered chapter text over the footage
    # --- Legacy: still load from old saved flows, but no longer generated/offered ---
    spotlight = "spotlight"
    click_ripple = "click_ripple"
    fade = "fade"


class BBox(BaseModel):
    """Absolute pixels within the recording's frame."""

    x: float
    y: float
    w: float
    h: float


class CutRange(BaseModel):
    """A span (absolute recording seconds) to remove from within a step. Set by
    drawing red 'cut' boxes on the editor timeline."""

    start: float
    end: float


class FlowStep(BaseModel):
    id: str
    order: int
    name: str
    timestamp_start: float   # seconds into recording
    timestamp_end: float
    frame_path: str          # representative frame
    narration_script: str
    animation_type: AnimationType
    bbox: BBox | None = None
    title_card_text: str | None = None
    audio_path: str | None = None
    clip_path: str | None = None
    # Steps inserted into an existing demo (editor "Insert step here") carry their
    # OWN little recording rather than living on the shared session.mp4 timeline.
    # When set, this step's footage comes from this file and timestamp_start/_end
    # are relative to it (0 → duration). When None, the step slices the shared
    # recording as usual.
    source_path: str | None = None
    # User-recorded voiceover for this step. When set (and the file exists), it
    # replaces the AI/TTS narration at render time.
    custom_voice_path: str | None = None
    # When the voiceover starts within the step, in seconds. Set visually by
    # dragging the step's voice block on the editor timeline. The clip always
    # holds its last frame so it lasts as long as the (delayed) voice.
    narration_delay: float = 0.0
    cuts: list[CutRange] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        k = self.timestamp_end - self.timestamp_start
        if getattr(self, "cuts", None):
            for c in self.cuts:
                k -= max(0.0, min(c.end, self.timestamp_end) - max(c.start, self.timestamp_start))
        return max(1.0, k)


class BrandConfig(BaseModel):
    """Branding, music, and outro settings applied at render time. Persisted on
    the flow and editable from the UI."""

    color: str = "#7F77DD"               # hex; drives intro/outro accents + highlight color
    logo_path: str | None = None         # uploaded logo, watermarked on the final video
    music: str | None = None             # background-music track name, or None for silence
    music_volume: float = 0.12           # 0..1, music level under the narration
    outro_enabled: bool = True
    outro_headline: str | None = None    # defaults to the flow title if empty
    outro_subtext: str | None = None     # e.g. a tagline
    outro_cta: str | None = None         # e.g. "Get started at example.com"


class FlowStatus(str, Enum):
    parsing = "parsing"
    recording = "recording"
    analyzing = "analyzing"
    ready = "ready"
    generating = "generating"
    rendering = "rendering"
    done = "done"
    error = "error"


class DemoFlow(BaseModel):
    id: str
    owner_id: str | None = None   # the user who created it (None for legacy flows)
    url: str
    instructions: str
    title: str = ""
    description: str = ""
    # Category / folder used to group demos on the dashboard. Empty → the dashboard
    # falls back to the website's domain so demos of the same site group together.
    label: str = ""
    raw_recording_path: str | None = None
    steps: list[FlowStep] = Field(default_factory=list)
    status: FlowStatus = FlowStatus.parsing
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    output_video_path: str | None = None
    error: str | None = None
    brand: BrandConfig = Field(default_factory=BrandConfig)
    voice: str = "male"   # AI narration voice key (see services.tts_service.VOICES)


# ---- API payloads ----


class GenerateRequest(BaseModel):
    flow_id: str


class RenderRequest(BaseModel):
    flow_id: str


class RegenerateScriptRequest(BaseModel):
    flow_id: str
    step_id: str
    tone: str = "professional"
    # The user's live draft from the editor textarea. When non-empty, the AI
    # polishes THIS text (keeping its meaning); when empty, it writes fresh
    # narration from the step. Falls back to the saved script if omitted.
    text: str | None = None


class PreviewAudioRequest(BaseModel):
    """Synthesize a step's (possibly unsaved) narration so the editor can play it
    over the clip and the user can dial in the sync."""

    text: str
    step_id: str | None = None
    voice: str | None = None   # narration voice key; falls back to the flow's voice


class FlowUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    label: str | None = None
    steps: list[FlowStep] | None = None
    brand: BrandConfig | None = None
    voice: str | None = None
