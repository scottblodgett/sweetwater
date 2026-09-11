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
    # M7. The cascade switch. Off means every work order is Tier 2, which is the M2 to M6 shape
    # and the baseline every ledger row is graded against. It ships off until the row in
    # `docs/model-routing.md` says the local model earned the job, and it is a config value
    # rather than a module constant because it is a cost and quality knob, not a safety one:
    # the rails and the gate do not read it. `routing.tier_for` is the only reader.
    tier1_enabled: bool = False
    # M7's measurement mode. When on, every packet Tier 1 judges is also judged by Tier 2 on
    # the identical page, and the pair lands in `logs/compare.jsonl` (`docs/Plan.md`). The
    # stored work order is still the one the cascade would have shipped; the shadow is a
    # receipt for grading. It SPENDS, at Tier-2 prices, on every Tier-1 packet. Off by default.
    tier_compare: bool = False

    # --- The ledger -------------------------------------------------------------
    # How many consecutive sweeps have to flag a sensor before it is an incident. The
    # deployed Sensor API invents a fresh reading on every call, so a healthy tank reads
    # empty one sweep in fifty and fine on the next; at 1 every such draw opened an incident,
    # paid a model for a work order, and resolved itself five minutes later. M4 measured that
    # at 10 to 24 per tick. At 2, a fault has to be there twice in a row, which the seven
    # permanently-bad sensors and every chaos scenario (minimum TTL two ticks) still are.
    # 1 restores open-on-first-sight, for a `--once` demo or a rail that is about something else.
    incident_confirm_sweeps: int = 2

    # --- Tick loop ------------------------------------------------------------
    # Cadence is start-to-start, not sleep-after-finish, so a busy tick does not drift the
    # schedule. Chaos TTLs are expressed in ticks and multiplied by this number at injection
    # time, so changing it rescales every scenario's lifetime with it: the two are one
    # decision, made here. See `executor.run_loop`.
    tick_interval_seconds: int = 300
    # The hard per-run spend ceiling, in dollars at `routing.PRICE_TABLE`. The loop HALTS
    # when the run's summed `cost_usd` reaches it and exits 4; it does not skip a tick and
    # carry on. Must be positive, and there is no value that means unlimited: 0 refuses to
    # start a spending loop. Ten dollars is roughly four storm ticks at M3's worst measured
    # cost, or a quiet day at a handful of new incidents per tick. `--once` is unaffected,
    # because a human is at the keyboard for that one.
    spend_ceiling_usd: float = 10.0
    # Per-upstream backoff, exponential and deterministic (one caller, so no herd to jitter
    # against). The loop itself never sleeps longer than one cadence: a sick upstream means
    # the stage that depends on it is skipped and named on the tick line, while the
    # heartbeat keeps its rhythm. Base 60s means one failure costs nothing at a 300s cadence
    # and three in a row skip the next tick; the cap is three ticks of silence toward one
    # dead service, not toward the ranch.
    backoff_base_seconds: float = 60.0
    backoff_cap_seconds: float = 900.0

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

    # --- The read API, M8 -----------------------------------------------------
    # `python main.py --api` binds here. Loopback by default: the window reaches it through
    # whatever fronts this box, and a default that listens on every interface is a default
    # that ships.
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # `name:secret` pairs, comma-separated. The bearer token `POST /ops/gate` requires, and the
    # name is what lands in `decided_by`. Read only by `--api`, which refuses to start without
    # at least one (exit 2): a server that boots and approves nothing is a server someone will
    # "fix" by removing the check. Never logged; `_SECRET_KEYS` in logger.py covers it.
    ops_api_token: str = ""
    # Allowed CORS origins, comma-separated. Empty means no cross-origin browser may read this
    # API, and empty is the default because M9's window on Vercel is the one origin that needs
    # it and a wildcard here would ship.
    api_cors_origins: str = ""
    # How often `/ops/stream` looks at `sw_ops.ticks` for a new row. A tick lands every five
    # minutes, so two seconds is generous; it is a knob so a rail can make it small.
    api_stream_poll_seconds: float = 2.0

    # --- Logging --------------------------------------------------------------
    log_level: str = "INFO"
    log_dir: str = "logs"
    # Size cap on `tick.jsonl` and `agent.jsonl` before rotation. Configurable so a
    # verification run can force a rotation for real (a 30-minute run writes about 18KB of
    # tick lines against a 10MB default) and prove the `run_id` join still works across
    # files, rather than simulating it.
    log_rotate_bytes: int = 10 * 1024 * 1024
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
    def cors_origins(self) -> tuple[str, ...]:
        return tuple(o.strip() for o in self.api_cors_origins.split(",") if o.strip())

    def ops_tokens(self) -> dict[str, str]:
        """`{secret: name}` from `OPS_API_TOKEN`, or a `ValueError` that says what is wrong.

        Raises rather than returning an empty mapping so `--api` cannot start with nobody able
        to approve a write and nothing saying so. Sixteen characters is the floor because a
        secret shorter than that is a password, and a password on a port is a guess away.
        """
        tokens: dict[str, str] = {}
        for pair in (p.strip() for p in self.ops_api_token.split(",") if p.strip()):
            name, sep, secret = pair.partition(":")
            if not sep or not name.strip() or not secret.strip():
                raise ValueError("OPS_API_TOKEN entries are name:secret, comma-separated")
            if len(secret.strip()) < 16:
                raise ValueError(f"OPS_API_TOKEN secret for {name.strip()!r} is shorter than 16 characters")
            tokens[secret.strip()] = name.strip()
        if not tokens:
            raise ValueError("OPS_API_TOKEN is empty. POST /ops/gate approves real writes on the ranch and needs at least one name:secret")
        return tokens

    @property
    def chaos_cohort(self) -> tuple[str, ...]:
        return tuple(a.strip() for a in self.chaos_animal_cohort.split(",") if a.strip())

    def missing_upstreams(self) -> list[str]:
        """Named rather than raising, so `--handshake` can report all of them at once."""
        return [name for name in ("mcp_url", "farm_api", "feed_api", "sensor_api", "care_api") if not getattr(self, name)]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
