"""Sector check filter.

Unlike other filters this one does not drop the ticker — it annotates the alert
with a 'sector_wide' flag when the sector ETF is also down meaningfully.
"""

from __future__ import annotations

from dataclasses import dataclass

from kangaroo.config import SectorSettings


@dataclass
class SectorResult:
    sector_wide: bool
    sector_pct_change: float | None = None


def apply_sector_check(
    ticker_sector: str,
    sector_pct_change: float,
    settings: SectorSettings,
) -> SectorResult:
    """Return sector_wide=True when the sector ETF is also down >= threshold. Pure."""
    is_wide = sector_pct_change <= -settings.flag_threshold_pct
    return SectorResult(sector_wide=is_wide, sector_pct_change=sector_pct_change)
