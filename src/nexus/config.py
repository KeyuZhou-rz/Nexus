from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _load_dotenv() -> None:
    """
    Load .env file from project root into os.environ.
    Only sets variables that are NOT already in the environment
    (shell env takes precedence over .env).
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# 模块导入时自动加载 .env
_load_dotenv()


@dataclass
class AppConfig:
    """Application configuration container."""
    data_dir: Path
    timezone: str = "local"
    google_credentials_path: Path | None = None
    google_token_path: Path | None = None
    feeds_path: Path | None = None
    google_calendar_sync_minutes: int = 15
    llm_enabled: bool = False
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout: int = 20
    llm_json_output: bool = True


def default_config() -> AppConfig:
    """Loads configuration from environment variables or default paths."""
    data_dir_env = os.getenv("NEXUS_DATA_DIR", "").strip()
    data_dir = Path(data_dir_env).expanduser() if data_dir_env else Path(__file__).resolve().parents[2] / "data"
    credentials_env = os.getenv("NEXUS_GOOGLE_CREDENTIALS")
    token_env = os.getenv("NEXUS_GOOGLE_TOKEN")
    google_credentials_path = Path(credentials_env) if credentials_env else data_dir / "google_client_secret.json"
    google_token_path = Path(token_env) if token_env else data_dir / "google_token.json"
    gcal_sync_raw = os.getenv("NEXUS_GCAL_SYNC_MINUTES", "15")
    llm_base_url = os.getenv("NEXUS_LLM_BASE_URL")
    llm_api_key = os.getenv("NEXUS_LLM_API_KEY")
    llm_model = os.getenv("NEXUS_LLM_MODEL")
    llm_enabled_raw = os.getenv("NEXUS_LLM_ENABLED", "0").strip().lower()
    llm_timeout_raw = os.getenv("NEXUS_LLM_TIMEOUT", "20")
    llm_json_output_raw = os.getenv("NEXUS_LLM_JSON_OUTPUT", "1").strip().lower()
    try:
        gcal_sync_minutes = int(gcal_sync_raw)
    except ValueError:
        gcal_sync_minutes = 15
    llm_enabled = llm_enabled_raw in {"1", "true", "yes", "on"}
    try:
        llm_timeout = int(llm_timeout_raw)
    except ValueError:
        llm_timeout = 20
    llm_json_output = llm_json_output_raw not in {"0", "false", "no"}
    return AppConfig(
        data_dir=data_dir,
        google_credentials_path=google_credentials_path,
        google_token_path=google_token_path,
        feeds_path=data_dir / "feeds.json",
        google_calendar_sync_minutes=gcal_sync_minutes,
        llm_enabled=llm_enabled,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        llm_json_output=llm_json_output,
    )
