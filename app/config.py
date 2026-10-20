"""Application settings, read from environment variables (prefix OPS_) or a local .env file.

Secrets are typed as SecretStr so they never show up in logs or error messages.
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPS_", env_file=ROOT / ".env", extra="ignore")

    # LLM backend: "openai" (cloud), "ollama" (self-hosted, OpenAI-compatible API) or "fake" (tests, offline demo)
    llm_provider: Literal["openai", "ollama", "fake"] = "fake"
    openai_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434/v1"
    chat_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 3

    # Access control: JSON object {sha256(api_key): {"name": ..., "role": ...}}; see `python -m app.cli create-key`
    api_keys: str = "{}"
    # Shared secret for HMAC signatures of incoming webhooks (Jira, n8n)
    webhook_secret: SecretStr | None = None

    # Outgoing integrations; when empty the connectors run in dry-run mode and only log what they would send
    teams_webhook_url: SecretStr | None = None
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: SecretStr | None = None

    # Knowledge search and review thresholds
    data_dir: Path = ROOT / "data"
    db_path: Path = ROOT / "data" / "ops.sqlite3"
    top_k: int = 4
    min_retrieval_score: float = 0.30
    review_confidence_threshold: float = 0.80


@lru_cache
def get_settings() -> Settings:
    return Settings()
