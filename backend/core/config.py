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
    # Leave the key blank to let the SDK resolve it from the environment.
    anthropic_api_key: str = ""
    analyst_model: str = "claude-opus-5"
    moderator_model: str = "claude-opus-5"
    # Effort controls how much the model thinks and how many tools it reaches
    # for, and it is the largest single cost lever in the system.
    #
    # Split deliberately: the research agents mostly read data and fill in a
    # schema, which "medium" handles well. The Moderator and Fact-Checker are
    # doing the actual judgement -- refereeing a debate and catching subtle
    # misrepresentation -- and that is where the extra thinking earns its cost.
    research_effort: str = "medium"
    judgment_effort: str = "high"

    @property
    def effort(self) -> str:
        """Back-compat default for anything that doesn't specify."""
        return self.research_effort

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
    def resolved_cache_dir(self) -> Path:
        """Cache directory, always anchored to the repo root.

        A relative CACHE_DIR would otherwise resolve against the *current
        working directory*, so running the CLI from backend/ and the API from
        the repo root would silently use two different caches.
        """
        path = Path(self.cache_dir)
        if not path.is_absolute():
            path = REPO_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cache_db_path(self) -> Path:
        return self.resolved_cache_dir / "tool_cache.sqlite3"


settings = Settings()
