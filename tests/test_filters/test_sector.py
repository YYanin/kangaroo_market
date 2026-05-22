"""Phase 5: Sector check filter tests."""

from __future__ import annotations

from kangaroo.config import SectorSettings
from kangaroo.filters.sector import apply_sector_check

_SETTINGS = SectorSettings(flag_threshold_pct=1.5)


class TestSectorCheck:
    def test_no_flag_when_sector_etf_flat(self) -> None:
        result = apply_sector_check("Technology", 0.0, _SETTINGS)
        assert result.sector_wide is False

    def test_no_flag_when_sector_etf_up(self) -> None:
        result = apply_sector_check("Technology", 1.8, _SETTINGS)
        assert result.sector_wide is False

    def test_flag_when_sector_etf_down_meaningfully(self) -> None:
        result = apply_sector_check("Technology", -2.0, _SETTINGS)
        assert result.sector_wide is True
        assert result.sector_pct_change == -2.0

    def test_no_flag_just_below_threshold(self) -> None:
        result = apply_sector_check("Technology", -1.4, _SETTINGS)
        assert result.sector_wide is False

    def test_flag_at_exact_threshold(self) -> None:
        result = apply_sector_check("Technology", -1.5, _SETTINGS)
        assert result.sector_wide is True
