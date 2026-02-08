from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass
class AppConfig:
    data_dir: Path
    timezone: str = "local"
    google_credentials_path: Path | None = None
    google_token_path: Path | None = None
    feeds_path: Path | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout: int = 20
    llm_json_output: bool = True


def default_config() -> AppConfig:
    data_dir = Path(__file__).resolve().parents[2] / "data"
    credentials_env = os.getenv("NEXUS_GOOGLE_CREDENTIALS")
    token_env = os.getenv("NEXUS_GOOGLE_TOKEN")
    google_credentials_path = Path(credentials_env) if credentials_env else data_dir / "google_client_secret.json"
    google_token_path = Path(token_env) if token_env else data_dir / "google_token.json"
    llm_base_url = os.getenv("NEXUS_LLM_BASE_URL")
    llm_api_key = os.getenv("NEXUS_LLM_API_KEY")
    llm_model = os.getenv("NEXUS_LLM_MODEL")
    llm_timeout_raw = os.getenv("NEXUS_LLM_TIMEOUT", "20")
    llm_json_output_raw = os.getenv("NEXUS_LLM_JSON_OUTPUT", "1").strip().lower()
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
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        llm_json_output=llm_json_output,
    )
