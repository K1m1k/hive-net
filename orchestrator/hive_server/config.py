# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""HiveNet server settings.

Everything is configured through environment variables (prefix HIVENET_), with
defaults that work out of the box on a desktop PC or a free-tier server.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name, default, cast=str):
    raw = os.environ.get("HIVENET_" + name)
    if raw is None:
        return default
    return cast(raw)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")).resolve())
    modules_dir: Path = field(default_factory=lambda: Path(_env("MODULES_DIR", str(Path(__file__).resolve().parent.parent / "modules"))))
    web_dir: Path = field(default_factory=lambda: Path(_env("WEB_DIR", str(Path(__file__).resolve().parent.parent / "web"))))

    # Packets
    chunk_max_bytes: int = field(default_factory=lambda: _env("CHUNK_MAX_BYTES", 64 * 1024, int))
    chunk_max_records: int = field(default_factory=lambda: _env("CHUNK_MAX_RECORDS", 2000, int))
    job_max_bytes: int = field(default_factory=lambda: _env("JOB_MAX_BYTES", 20 * 1024 * 1024, int))

    # Leases (seconds): the node proposes, the server clamps
    lease_min_s: int = field(default_factory=lambda: _env("LEASE_MIN_S", 30, int))
    lease_max_s: int = field(default_factory=lambda: _env("LEASE_MAX_S", 1800, int))
    lease_default_s: int = field(default_factory=lambda: _env("LEASE_DEFAULT_S", 120, int))
    max_attempts: int = field(default_factory=lambda: _env("MAX_ATTEMPTS", 5, int))
    max_packets_per_request: int = field(default_factory=lambda: _env("MAX_PACKETS", 8, int))

    # Spot-check verification
    verify_p_reliable: float = field(default_factory=lambda: _env("VERIFY_P_RELIABLE", 0.05, float))
    verify_p_normal: float = field(default_factory=lambda: _env("VERIFY_P_NORMAL", 0.10, float))
    verify_p_new: float = field(default_factory=lambda: _env("VERIFY_P_NEW", 0.30, float))
    verify_wait_s: int = field(default_factory=lambda: _env("VERIFY_WAIT_S", 600, int))

    # Honey (internal credits)
    welcome_honey: int = field(default_factory=lambda: _env("WELCOME_HONEY", 100, int))
    daily_cap: int = field(default_factory=lambda: _env("DAILY_CAP", 2000, int))
    daily_cap_new: int = field(default_factory=lambda: _env("DAILY_CAP_NEW", 200, int))
    cached_cost_factor: float = field(default_factory=lambda: _env("CACHED_COST_FACTOR", 0.1, float))

    # How long pending originals are kept (seconds)
    staging_ttl_s: int = field(default_factory=lambda: _env("STAGING_TTL_S", 7 * 24 * 3600, int))

    # Weekly community goal (cells)
    weekly_goal: int = field(default_factory=lambda: _env("WEEKLY_GOAL", 10000, int))

    # Rate limits (requests per minute)
    rate_auth: int = field(default_factory=lambda: _env("RATE_AUTH", 10, int))
    rate_node: int = field(default_factory=lambda: _env("RATE_NODE", 1200, int))
    rate_api: int = field(default_factory=lambda: _env("RATE_API", 300, int))

    maintenance_every_s: float = field(default_factory=lambda: _env("MAINTENANCE_EVERY_S", 5.0, float))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hivenet.db"

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"
