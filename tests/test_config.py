"""Phase 1: Config loading tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from kangaroo.config import load_settings, reset_settings


@pytest.fixture(autouse=True)
def _reset() -> None:  # type: ignore[return]
    reset_settings()
    yield
    reset_settings()


class TestConfigLoading:
    def test_config_loads_from_yaml(self, tmp_path: Path) -> None:
        cfg = {
            "universe": {"min_pct_drop": 5.5, "min_relative_volume": 3.0, "max_count": 50},
            "quality": {"min_market_cap": 20_000_000_000},
            "ladder": {"step_pct": 4.0, "max_rungs": 3},
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(cfg))

        settings = load_settings(config_path=config_file)

        assert settings.universe.min_pct_drop == 5.5
        assert settings.universe.min_relative_volume == 3.0
        assert settings.universe.max_count == 50
        assert settings.quality.min_market_cap == 20_000_000_000
        assert settings.ladder.step_pct == 4.0
        assert settings.ladder.max_rungs == 3

    def test_defaults_are_used_when_key_absent(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({}))

        settings = load_settings(config_path=config_file)

        assert settings.universe.min_pct_drop == 4.0
        assert settings.ladder.step_pct == 3.0
        assert settings.earnings.blackout_days == 5

    def test_secrets_loaded_from_env_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("POLYGON_API_KEY=test_key_123\n")
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({}))

        # Ensure env var is not already set
        os.environ.pop("POLYGON_API_KEY", None)

        settings = load_settings(config_path=config_file, env_file=env_file)
        assert settings.polygon_api_key == "test_key_123"

        os.environ.pop("POLYGON_API_KEY", None)
