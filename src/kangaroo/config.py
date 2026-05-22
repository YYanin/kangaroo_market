"""Unified settings loader: reads config.yaml for thresholds and .env for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _load_env(env_file: Path | None) -> None:
    """Load .env into os.environ (idempotent)."""
    target = env_file or Path(".env")
    if not target.exists():
        return
    with target.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


@dataclass
class UniverseSettings:
    min_pct_drop: float = 4.0
    min_relative_volume: float = 2.0
    max_count: int = 100


@dataclass
class QualitySettings:
    min_market_cap: float = 10_000_000_000.0
    min_avg_daily_dollar_volume: float = 50_000_000.0
    require_positive_ttm_income: bool = True
    allowed_security_types: list[str] = field(default_factory=lambda: ["CS", "Common Stock"])


@dataclass
class SetupSettings:
    min_drawdown_pct: float = 8.0
    max_drawdown_pct: float = 30.0
    max_pct_below_200dma: float = 15.0
    max_rsi_14: float = 40.0


@dataclass
class EarningsSettings:
    blackout_days: int = 5


@dataclass
class SectorSettings:
    flag_threshold_pct: float = 1.5


@dataclass
class NewsSettings:
    lookback_hours: int = 24
    max_articles: int = 3
    headline_cache_ttl_minutes: int = 30
    article_cache_ttl_minutes: int = 240


@dataclass
class LadderSettings:
    step_pct: float = 3.0
    max_rungs: int = 5
    tracking_window_days: int = 10
    recovery_exit_pct: float = 4.0
    structural_damage_drawdown_pct: float = 30.0
    structural_damage_below_200dma_pct: float = 15.0


@dataclass
class NotificationSettings:
    provider: str = "pushbullet"


@dataclass
class DbSettings:
    path: str = "kangaroo.db"


@dataclass
class Settings:
    """Single entry point for all configuration values."""

    # Secrets (from .env)
    polygon_api_key: str = ""
    finnhub_api_key: str = ""
    pushbullet_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Sub-settings (from config.yaml)
    universe: UniverseSettings = field(default_factory=UniverseSettings)
    quality: QualitySettings = field(default_factory=QualitySettings)
    setup: SetupSettings = field(default_factory=SetupSettings)
    earnings: EarningsSettings = field(default_factory=EarningsSettings)
    sector: SectorSettings = field(default_factory=SectorSettings)
    news: NewsSettings = field(default_factory=NewsSettings)
    ladder: LadderSettings = field(default_factory=LadderSettings)
    notification: NotificationSettings = field(default_factory=NotificationSettings)
    db: DbSettings = field(default_factory=DbSettings)

    @property
    def db_path(self) -> str:
        return os.environ.get("DB_PATH") or self.db.path


def _from_dict(cls: type, data: dict[str, Any]) -> Any:
    """Construct a dataclass from a dict, ignoring unknown keys."""
    import dataclasses

    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def load_settings(
    config_path: Path | None = None,
    env_file: Path | None = None,
) -> Settings:
    """Load settings from config.yaml and .env."""
    _load_env(env_file)

    yaml_path = config_path or (Path(__file__).parent.parent.parent / "config.yaml")
    raw: dict[str, Any] = {}
    if yaml_path.exists():
        raw = _load_yaml(yaml_path)

    return Settings(
        polygon_api_key=os.environ.get("POLYGON_API_KEY", ""),
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY", ""),
        pushbullet_token=os.environ.get("PUSHBULLET_TOKEN", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        universe=_from_dict(UniverseSettings, raw.get("universe", {})),
        quality=_from_dict(QualitySettings, raw.get("quality", {})),
        setup=_from_dict(SetupSettings, raw.get("setup", {})),
        earnings=_from_dict(EarningsSettings, raw.get("earnings", {})),
        sector=_from_dict(SectorSettings, raw.get("sector", {})),
        news=_from_dict(NewsSettings, raw.get("news", {})),
        ladder=_from_dict(LadderSettings, raw.get("ladder", {})),
        notification=_from_dict(NotificationSettings, raw.get("notification", {})),
        db=_from_dict(DbSettings, raw.get("db", {})),
    )


_settings: Settings | None = None


def get_settings(config_path: Path | None = None) -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings(config_path)
    return _settings


def reset_settings() -> None:
    """Clear the cached singleton (used in tests)."""
    global _settings
    _settings = None
