"""Every upstream, model, and knob read from the environment exactly once.

No endpoint is hardcoded anywhere else in the tree. The deployed ranch moved
hosts twice already in this project's life; a URL in a source file is a URL you
will find with grep at the worst possible moment.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    # --- Upstream ranch (deployed, frozen, not ours) --------------------------
    mcp_url: str = Field(default="", description="Lambda Function URL of the deployed MCP server.")
    farm_api: str = ""
    feed_api: str = ""
    sensor_api: str = ""
    care_api: str = ""

    upstream_timeout_ms: int = 15_000
    sweep_concurrency: int = 20

    # --- Agent state ----------------------------------------------------------
    database_url: str = ""
    database_url_test: str = ""

    # --- Models ---------------------------------------------------------------
    anthropic_api_key: str = ""
    tier2_model: str = "claude-opus-5"
    ollama_base_url: str = "http://localhost:11434"
    tier1_model: str = "gemma4:e4b"
    ollama_num_ctx: int = 16_384

    # --- Tick loop ------------------------------------------------------------
    tick_interval_seconds: int = 300

    # --- Chaos ----------------------------------------------------------------
    chaos_enabled: bool = True
    chaos_seed: int = 1
    chaos_allow_writes: bool = False
    chaos_animal_cohort: str = ""

    # --- Logging --------------------------------------------------------------
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_console_pretty: bool = True
    log_transcripts: bool = False

    @field_validator("mcp_url", "farm_api", "feed_api", "sensor_api", "care_api")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        # Every call site builds paths as f"{base}/sensors". A trailing slash here
        # yields "//sensors", which API Gateway answers with a 404 that reads like
        # a missing route rather than a formatting mistake.
        return v.rstrip("/")

    @property
    def upstream_timeout_s(self) -> float:
        return self.upstream_timeout_ms / 1000.0

    @property
    def log_path(self) -> Path:
        p = Path(self.log_dir)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def chaos_cohort(self) -> tuple[str, ...]:
        return tuple(a.strip() for a in self.chaos_animal_cohort.split(",") if a.strip())

    def missing_upstreams(self) -> list[str]:
        """Named rather than raising, so `--handshake` can report all of them at once."""
        return [name for name in ("mcp_url", "farm_api", "feed_api", "sensor_api", "care_api") if not getattr(self, name)]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
