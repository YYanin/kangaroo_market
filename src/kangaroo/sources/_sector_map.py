"""Sector name → representative ETF ticker mapping."""

from __future__ import annotations

SECTOR_ETF_MAP: dict[str, str] = {
    # Technology
    "Technology": "XLK",
    "Information Technology": "XLK",
    # Financials
    "Financials": "XLF",
    "Financial Services": "XLF",
    "Banking": "XLF",
    # Energy
    "Energy": "XLE",
    # Healthcare
    "Healthcare": "XLV",
    "Health Care": "XLV",
    # Consumer Discretionary
    "Consumer Discretionary": "XLY",
    "Consumer Cyclical": "XLY",
    # Consumer Staples
    "Consumer Staples": "XLP",
    "Consumer Defensive": "XLP",
    # Industrials
    "Industrials": "XLI",
    # Materials
    "Materials": "XLB",
    "Basic Materials": "XLB",
    # Real Estate
    "Real Estate": "XLRE",
    # Utilities
    "Utilities": "XLU",
    # Communication Services
    "Communication Services": "XLC",
    "Communication": "XLC",
    # Catch-all
    "default": "SPY",
}
