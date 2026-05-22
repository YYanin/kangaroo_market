"""Keyword blocklist filter — the most important safety mechanism in the system.

IMPORTANT (from AGENTS.md):
    This list is append-only. Existing terms are never silently removed.
    If a removal is requested, confirm with the user and add a comment explaining why.
    New phrasing variants can be added; no term is ever deleted without explicit review.
"""

from __future__ import annotations

from kangaroo.filters.quality import FilterResult
from kangaroo.sources.news import Headline

# All terms are matched case-insensitively.
# Variants that differ only in phrasing are listed separately for clarity.
BLOCKLIST_TERMS: tuple[str, ...] = (
    # Regulatory investigations
    "SEC investigation",
    "SEC subpoena",
    "DOJ investigation",
    "DOJ subpoena",
    "CFTC investigation",
    "FTC investigation",
    # Accounting / disclosure failures
    "restatement",
    "material weakness",
    "accounting irregularity",
    "going concern",
    "internal controls failure",
    # Insolvency / delisting
    "Chapter 11",
    "Chapter 7",
    "bankruptcy filing",
    "delisting notice",
    "going private",
    "receivership",
    # Fraud
    "Ponzi",
    "fraud",
    "accounting fraud",
    "securities fraud",
    "embezzlement",
    # Executive / auditor departures under duress
    "CFO resigned",
    "CFO resigns",
    "CEO resigned",
    "CEO resigns",
    "auditor resigned",
    "auditor resigns",
    "auditor dismissed",
    "Chief Financial Officer has resigned",
    "Chief Executive Officer has resigned",
    # Guidance suspensions
    "guidance withdrawn",
    "withdraws guidance",
    "suspends guidance",
    "withdrawing guidance",
    # Other structural damage signals
    "force majeure",
    "liquidity crisis",
    "covenant breach",
    "debt default",
)

_CONTEXT_WINDOW = 80  # characters of surrounding text to include in the reason


def _find_term(text: str, term: str) -> tuple[bool, str | None]:
    """Return (found, context_snippet). Case-insensitive."""
    lower_text = text.lower()
    lower_term = term.lower()
    idx = lower_text.find(lower_term)
    if idx == -1:
        return False, None
    start = max(0, idx - _CONTEXT_WINDOW // 2)
    end = min(len(text), idx + len(term) + _CONTEXT_WINDOW // 2)
    snippet = text[start:end].replace("\n", " ").strip()
    return True, snippet


def apply_blocklist(
    ticker: str,
    headlines: list[Headline],
    articles: list[str],
) -> FilterResult:
    """Return passed=False if any blocklist term appears in headlines or articles.

    The reason string includes the offending term and an 80-character context snippet.
    """
    all_texts = [h.title for h in headlines] + articles

    for text in all_texts:
        for term in BLOCKLIST_TERMS:
            found, snippet = _find_term(text, term)
            if found:
                return FilterResult(
                    passed=False,
                    reason=f"blocklist_hit: {term!r} in: ...{snippet}...",
                )

    return FilterResult(passed=True)
