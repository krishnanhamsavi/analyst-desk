"""Central configuration. Everything tunable lives here, loaded from .env.

Nothing in the codebase should read os.environ directly -- import `settings`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (backend/core/config.py -> repo root)
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    anthropic_api_key: str = ""
    analyst_model: str = "claude-sonnet-5"
    moderator_model: str = "claude-opus-5"

    # --- Data sources ---
    # SEC EDGAR rejects requests without a descriptive UA containing contact info.
    sec_user_agent: str = "AnalystDesk/0.1 (contact@example.com)"

    # --- Storage ---
    database_url: str = "sqlite:///./analyst_desk.db"
    cache_dir: Path = REPO_ROOT / "data" / "cache"
    cache_ttl_hours: int = 12

    # --- Runtime ---
    demo_mode: bool = False
    log_level: str = "INFO"

    @property
    def cache_db_path(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / "tool_cache.sqlite3"


settings = Settings()
