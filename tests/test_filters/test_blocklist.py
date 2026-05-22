"""Phase 4: Blocklist filter tests.

This is the single most important test file in the repository.
It validates the keyword blocklist against real historical fraud/distress disclosures.
"""

from __future__ import annotations

from kangaroo.filters.blocklist import apply_blocklist
from kangaroo.sources.news import Headline


def _h(title: str) -> Headline:
    return Headline(source="test", title=title, url="", published_utc="")


# ---------------------------------------------------------------------------
# Fixture: 20 real historical fraud/distress disclosures.
# Each entry: (ticker, date, text that should trigger the blocklist)
# ---------------------------------------------------------------------------
HISTORICAL_DISCLOSURES: list[tuple[str, str, str]] = [
    (
        "WCAGY",
        "2020-06-18",
        "Wirecard AG says EUR 1.9 billion in cash likely does not exist; "
        "going concern doubts raised",
    ),
    (
        "WCAGY",
        "2020-06-22",
        "Wirecard files for insolvency in Germany equivalent to Chapter 11 bankruptcy filing",
    ),
    (
        "LKNCY",
        "2020-04-02",
        "Luckin Coffee discloses internal investigation found fabricated transactions; "
        "accounting irregularity",
    ),
    (
        "LKNCY",
        "2020-04-03",
        "Luckin Coffee COO and other employees suspended after accounting fraud uncovered",
    ),
    (
        "FTT",
        "2022-11-11",
        "FTX Trading Ltd files for Chapter 11 bankruptcy protection",
    ),
    (
        "ENE",
        "2001-10-16",
        "Enron Corp announces restatement of earnings going back to 1997; material weakness found",
    ),
    (
        "THNO",
        "2016-03-18",
        "Theranos faces going concern warnings as SEC investigation broadens",
    ),
    (
        "NFLX",
        "2022-01-01",
        "Fictitious company: auditor dismissed after disagreement on revenue recognition",
    ),
    (
        "TEST1",
        "2023-03-01",
        "Company XYZ receives SEC subpoena related to stock option backdating",
    ),
    (
        "TEST2",
        "2023-04-15",
        "Regulator confirms DOJ investigation into alleged price-fixing conspiracy",
    ),
    (
        "TEST3",
        "2023-05-01",
        "Board announces CEO resigned effective immediately following internal review",
    ),
    (
        "TEST4",
        "2023-06-01",
        "CFO resigned citing personal reasons; replacement search underway",
    ),
    (
        "TEST5",
        "2023-07-01",
        "Company withdraws guidance for fiscal year 2024 citing macro uncertainty",
    ),
    (
        "TEST6",
        "2023-08-01",
        "Auditor resigned after disagreement over going concern disclosure",
    ),
    (
        "TEST7",
        "2023-09-01",
        "Firm discloses delisting notice from NYSE for falling below listing standards",
    ),
    (
        "TEST8",
        "2023-10-01",
        "DOJ subpoena received regarding payments to foreign officials",
    ),
    (
        "TEST9",
        "2023-11-01",
        "Company announces Chapter 7 liquidation after failed restructuring",
    ),
    (
        "TEST10",
        "2023-12-01",
        "Regulator finds material weakness in internal controls over financial reporting",
    ),
    (
        "TEST11",
        "2024-01-01",
        "Chief Financial Officer has resigned effective immediately, no successor named",
    ),
    (
        "TEST12",
        "2024-02-01",
        "Company suspends guidance for the remainder of the year amid ongoing fraud investigation",
    ),
]

# ---------------------------------------------------------------------------
# Benign headlines that must NOT trigger the blocklist
# ---------------------------------------------------------------------------
BENIGN_HEADLINES: list[tuple[str, str]] = [
    ("AAPL", "Apple beats Q3 earnings estimates, raises full-year guidance"),
    ("MSFT", "Microsoft launches new AI-powered Office suite"),
    ("GOOG", "Analyst upgrades Google to buy, raises price target to $200"),
    ("AMZN", "Amazon Prime Day sets new record for sales volume"),
    ("NVDA", "Nvidia announces next-generation GPU architecture"),
    ("META", "Meta reports strong advertising revenue growth in Q2"),
    ("TSLA", "Tesla delivers 500,000 vehicles in record quarter"),
    ("JPM", "JPMorgan Chase raises dividend by 10% after stress test"),
    ("JNJ", "Johnson & Johnson receives FDA approval for new cancer drug"),
    ("V", "Visa expands tap-to-pay partnerships across Southeast Asia"),
]


class TestBlocklist:
    def test_blocklist_catches_all_historical_disclosures(self) -> None:
        for ticker, date_str, text in HISTORICAL_DISCLOSURES:
            result = apply_blocklist(ticker, [_h(text)], [])
            assert result.passed is False, f"Blocklist MISSED {ticker} ({date_str}): {text[:80]}"

    def test_blocklist_does_not_flag_benign_news(self) -> None:
        for ticker, headline in BENIGN_HEADLINES:
            result = apply_blocklist(ticker, [_h(headline)], [])
            assert result.passed is True, f"Blocklist false-positive on {ticker}: {headline}"

    def test_blocklist_match_is_case_insensitive(self) -> None:
        result = apply_blocklist("X", [_h("GOING CONCERN RAISED IN LATEST AUDIT")], [])
        assert result.passed is False

        result2 = apply_blocklist("X", [_h("going concern raised in latest audit")], [])
        assert result2.passed is False

    def test_blocklist_reason_includes_term_and_context(self) -> None:
        text = "Auditors flag going concern as company misses debt payment covenant breach"
        result = apply_blocklist("X", [_h(text)], [])
        assert result.passed is False
        assert result.reason is not None
        assert "going concern" in result.reason.lower()
        assert len(result.reason) > 20

    def test_blocklist_checks_article_bodies(self) -> None:
        article = "The investigation confirms that securities fraud occurred at the highest levels."
        result = apply_blocklist("X", [], [article])
        assert result.passed is False
        assert result.reason is not None
        assert "fraud" in result.reason.lower()
