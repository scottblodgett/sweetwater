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
    # Off by default, and that default is load-bearing rather than timid. An overlay
    # firing underneath a cost measurement turns the measurement into noise, and a
    # fixture that arms itself is a fixture nobody trusts. Chaos is switched on for a
    # demo, deliberately, by a person who wants the ranch to break.
    chaos_enabled: bool = False
    chaos_seed: int = 1
    chaos_allow_writes: bool = False
    chaos_animal_cohort: str = ""
    # Ceiling on simultaneously active events. Without it, hour three of a demo is a
    # ranch where everything is broken, which reads as a bug in the monitor rather than
    # as a ranch in trouble. Healing is what makes the feed feel alive; a ceiling is what
    # keeps there being something left to heal.
    chaos_max_active: int = 6
    # TTL in ticks rather than seconds, because a scenario's lifetime is meaningful in
    # units of "how many times will the sweep see this" and a tick interval can change.
    chaos_default_ttl_ticks: int = 3
    # Deterministic cadence, not a fire probability. A probability is one more thing that
    # drifts away from the seed the moment someone tunes it.
    chaos_ticks_between_events: int = 1

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

    @field_validator("database_url", "database_url_test")
    @classmethod
    def _force_asyncpg_driver(cls, v: str) -> str:
        # SQLAlchemy picks its DBAPI from the URL scheme, and bare `postgresql://`
        # means psycopg2, which is not installed and never will be. The failure is a
        # ModuleNotFoundError at engine-creation time that reads like a missing
        # dependency rather than a URL typo. Both `.env` values arrive bare from every
        # tool that hands out a connection string (Supabase's dashboard included), so
        # the upgrade happens here once instead of at each `create_async_engine`.
        for scheme in ("postgresql://", "postgres://"):
            if v.startswith(scheme):
                return "postgresql+asyncpg://" + v[len(scheme) :]
        return v

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
