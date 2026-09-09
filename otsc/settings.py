"""Non-secret preferences and per-role macOS Keychain credentials."""

import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from otsc.privacy import app_directory, private_write

ProviderName = Literal["disabled", "codex", "openai", "gemini", "anthropic", "groq", "compatible"]
BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic": "https://api.anthropic.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}
KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "compatible": "OTSC_COMPATIBLE_API_KEY",
}


class ModelChoice(BaseModel):
    provider: ProviderName = "disabled"
    model: str = ""
    base_url: str = ""
    send_images: bool = False
    max_tokens: int = Field(default=2000, ge=256, le=16000)
    reasoning: str = ""

    @model_validator(mode="after")
    def validate_endpoint(self):
        if self.base_url:
            parsed = urlsplit(self.base_url)
            if parsed.username or parsed.password:
                raise ValueError("Keep credentials in Keychain, not in an endpoint URL")
            if parsed.query or parsed.fragment:
                raise ValueError("Use an API base URL without query parameters or a fragment")
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("Use HTTPS, or HTTP for a local model endpoint")
        return self

    def endpoint(self):
        return (self.base_url or BASE_URLS.get(self.provider, "")).rstrip("/")

    def key_slot(self, role):
        return role + ":" + self.provider + ":" + hashlib.sha256(self.endpoint().encode()).hexdigest()[:12]


class Settings(BaseModel):
    configured: bool = False
    quick: ModelChoice = Field(
        default_factory=lambda: ModelChoice(
            provider="codex", model="gpt-5.3-codex-spark", reasoning="low", max_tokens=1800
        )
    )
    deep: ModelChoice = Field(
        default_factory=lambda: ModelChoice(
            provider="codex", model="gpt-5.5", reasoning="low", send_images=True, max_tokens=10000
        )
    )
    transcription: Literal["disabled", "local", "groq", "openai"] = "disabled"
    transcription_model: str = ""
    local_asr_model: str = "mlx-community/parakeet-tdt-0.6b-v3"
    capture_screen: bool = True
    microphone: bool = False
    system_audio: bool = False
    microphone_device: int | None = None
    microphone_role: Literal["primary_user", "other_people", "uncertain"] = "primary_user"
    system_role: Literal["primary_user", "other_people", "uncertain"] = "other_people"
    interval_seconds: int = Field(default=30, ge=5, le=300)
    audio_chunk_seconds: int = Field(default=5, ge=2, le=20)
    hourly_requests: int = Field(default=120, ge=1, le=1000)
    screen_region: tuple[int, int, int, int] | None = None
    window_bounds: tuple[int, int, int, int] = (120, 100, 1050, 800)
    goal: str = ""
    project_folder: str = ""
    local_session_memory: bool = False
    operational_metadata: bool = True
    inspection_enabled: bool = False


def load_settings(directory: Path | None = None) -> Settings:
    path = (directory or app_directory()) / "settings.json"
    if not path.exists():
        return Settings()
    return Settings.model_validate_json(path.read_text())


def save_settings(settings: Settings, directory: Path | None = None):
    private_write((directory or app_directory()) / "settings.json", settings.model_dump_json(indent=2))


class Credentials:
    service = "OverTheShoulderCoder"

    def get(self, choice: ModelChoice, role: str) -> str:
        try:
            import Security

            query = {
                Security.kSecClass: Security.kSecClassGenericPassword,
                Security.kSecAttrService: self.service,
                Security.kSecAttrAccount: choice.key_slot(role),
                Security.kSecReturnData: True,
                Security.kSecMatchLimit: Security.kSecMatchLimitOne,
            }
            status, data = Security.SecItemCopyMatching(query, None)
            if status == 0 and data is not None:
                return bytes(data).decode()
        except ImportError:
            pass
        value = os.getenv(KEY_ENV.get(choice.provider, "OTSC_NO_KEY"), "")
        if choice.provider == "gemini" and not value:
            value = os.getenv("GOOGLE_API_KEY", "")
        return value

    def set(self, choice: ModelChoice, role: str, secret: str):
        import Security

        query = {
            Security.kSecClass: Security.kSecClassGenericPassword,
            Security.kSecAttrService: self.service,
            Security.kSecAttrAccount: choice.key_slot(role),
        }
        if not secret:
            status = Security.SecItemDelete(query)
            if status not in (0, -25300):
                raise RuntimeError(f"Keychain could not remove this credential ({status})")
            return
        value = {Security.kSecValueData: secret.encode()}
        status = Security.SecItemUpdate(query, value)
        if status == -25300:
            outcome = Security.SecItemAdd({**query, **value}, None)
            status = outcome[0] if isinstance(outcome, tuple) else outcome
        if status != 0:
            raise RuntimeError(f"Keychain could not save this credential ({status})")


def settings_summary(settings: Settings) -> str:
    return json.dumps(settings.model_dump(), ensure_ascii=False, indent=2)
