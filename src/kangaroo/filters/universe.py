"""Universe filter: reduce raw decliner list to candidates worth further evaluation."""

from __future__ import annotations

from kangaroo.config import UniverseSettings
from kangaroo.sources.market_data import DeclinerRecord


def apply_universe_filter(
    decliners: list[DeclinerRecord],
    settings: UniverseSettings,
) -> list[DeclinerRecord]:
    """Return decliners that pass pct-drop and relative-volume thresholds, capped at max_count.

    Pure function — no I/O.
    """
    passed = [
        d
        for d in decliners
        if d.pct_change_day <= -settings.min_pct_drop
        and d.relative_volume >= settings.min_relative_volume
    ]
    passed.sort(key=lambda d: d.pct_change_day)
    return passed[: settings.max_count]
