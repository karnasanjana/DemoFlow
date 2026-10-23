"""Settings from .env via pydantic-settings."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    google_tts_api_key: str = ""
    # Auth: secret for signing session cookies (auto-generated & persisted if blank);
    # google_client_id enables the "Sign in with Google" button when set.
    auth_secret: str = ""
    google_client_id: str = ""
    output_dir: Path = REPO_ROOT / "output"
    port: int = 8000
    headless: bool = False  # show the browser during recording so the user can watch
    # Auto-recording pace. slow_mo is the delay (ms) Playwright inserts before each
    # action so the video is watchable; settle is how long to wait after a click/nav
    # for the page to render. Lower = faster recording. Tune in .env if the run feels
    # sluggish (record_slow_mo=120, record_settle_s=0.4) or too rushed.
    record_slow_mo: int = 250
    record_settle_s: float = 0.6
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("output_dir", mode="before")
    @classmethod
    def resolve_output_dir(cls, v: object) -> object:
        if isinstance(v, str):
            p = Path(v)
            return p if p.is_absolute() else (REPO_ROOT / p).resolve()
        return v


settings = Settings()
