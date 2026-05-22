# Design Document: Kangaroo Research Assistant (v1.0)

## 1. Executive Summary

This is a **research and alerting tool**, not an autonomous trading bot. Its job is to surface high-quality "Kangaroo" candidates — well-established companies that have recently dropped on news that may be transient — and present them to the user with enough supporting evidence to make a discretionary trading decision.

The user (a retail investor) places all trades manually through their broker. The system never connects to a brokerage and never places, modifies, or cancels orders. This is a deliberate scope choice: removing autonomous execution eliminates the entire category of risks (PDT compliance, execution latency, runaway logic, kill-switch design) that dominate autonomous trading bot designs and lets the project focus on what actually adds value — better screening and faster, better-informed research.

**Core principle:** the bot is a research assistant. The user is the trader.

## 2. Goals and Non-Goals

### Goals

- Reduce the daily universe of US-listed equities to a short list of Kangaroo candidates that match the user's quality and setup criteria.
- For each candidate, gather and present the supporting evidence (news, fundamentals, chart context, sector context) in one place so the user can make a decision in 1-2 minutes per alert rather than 15-30.
- Suppress alerts where known disqualifying conditions exist (pending earnings, fraud-related keywords, sector-wide selloffs).
- Log every alert and its subsequent realized returns so the user can measure whether the system is actually helping over time.

### Non-Goals

- The system does not place trades.
- The system does not connect to a broker.
- The system does not size positions, set stop losses, or manage portfolio risk. Those are the user's responsibilities, performed in their broker.
- The system does not aim to outperform the market in expectation. It aims to make the user's existing Kangaroo strategy faster and more disciplined.
- The system is not a backtesting framework. Backtesting is a separate, occasional activity (see Section 9).

## 3. Architecture

The architecture is intentionally minimal. Everything runs on the Mac Mini M4. The RTX 3060 Ti PC is used only for occasional research and backtesting, not as part of the live system.

### Components

**Scheduler.** A single process that runs the alert pipeline on a fixed cadence (every 30 minutes during US market hours, plus one end-of-day run at market close). Implemented with `apscheduler` or a simple `asyncio` loop.

**Alert Pipeline.** A linear sequence of filters and enrichments, executed for each candidate ticker:

1. **Universe scan** — pull the day's largest decliners from a market data API.
2. **Quality filter** — drop tickers that don't meet "well-established" criteria.
3. **Setup filter** — drop tickers whose drawdown shape doesn't match the Kangaroo pattern.
4. **Earnings blackout** — drop tickers within 5 trading days of an earnings release.
5. **Sector check** — drop tickers whose decline is part of a sector-wide selloff.
6. **News retrieval** — pull recent headlines and articles for survivors.
7. **Keyword blocklist** — drop tickers with disqualifying terms in any recent headline or filing.
8. **LLM classification** *(Phase 2 only)* — score remaining tickers for transient vs. structural news.
9. **Persistence** — write the candidate (and the reason for any drops along the way) to SQLite.
10. **Notification** — push a notification for high-priority candidates.

**SQLite database.** A single local database file storing alerts, their evidence, and their realized returns over time. SQLite is the right choice here — single-user, single-process, no backup or replication concerns, file-based.

**Web dashboard.** A FastAPI application running locally on the Mac Mini, serving a single HTML page that shows today's alerts, recent alerts with realized returns, hit rate statistics, and a search interface over the historical alert log. Accessed from the user's phone or laptop on the home network.

**Notification service.** Pushbullet or Telegram (whichever the user already has set up). Sends a short summary; the user clicks through to the dashboard for full evidence.

### What is deliberately not in this architecture

- No microservices, actor model, message queue, or Redis. A single Python process with `asyncio` for I/O concurrency is sufficient at this scale.
- No second machine in the live path. Cross-machine latency is not a problem worth solving for a tool whose users react in minutes, not milliseconds.
- No production-grade backtesting framework like `nautilus_trader`. Backtesting is done ad-hoc with `vectorbt` on the RTX PC.
- No kill switch, PDT counter, or order throttling. The system never sends orders.

## 4. Data Sources

| Data | Provider | Notes |
|---|---|---|
| Market data (decliners, OHLCV, fundamentals) | Polygon.io or Finnhub.io | Either has a tier sufficient for end-of-day and 30-minute-delayed intraday data. Real-time is not required. |
| News headlines and full text | Finnhub.io news endpoint | Cleaner JSON than scraping; covers most major outlets. |
| Earnings calendar | Finnhub.io or the same market data provider | Used by the earnings blackout filter. |
| Sector / industry classification | Same as market data | Used by the sector check filter. |

API keys are stored in a `.env` file at the project root, loaded with `python-dotenv`, and `.env` is in `.gitignore`. No keys in code, ever.

## 5. The Filtering Pipeline

The filters are ordered from cheapest to most expensive. Each filter eliminates tickers so that the expensive operations (news retrieval, LLM inference) run on the smallest possible set.

### 5.1 Universe scan

Each run, pull all US-listed common stocks down at least 4% on the day with at least 2x their 20-day average volume. Cap the result at the top 100 by absolute decline to keep downstream cost predictable. A 4% / 2x threshold is intentionally lower than the original "5% / 3x" — the goal is to catch more candidates and let the downstream filters do the work.

### 5.2 Quality filter

A ticker passes only if **all** of the following are true:

- Market cap ≥ $10B at the start of the day.
- Trailing-twelve-month net income > 0 (i.e., the company is profitable).
- Average daily dollar volume over the last 30 days ≥ $50M (tradable liquidity).
- The ticker is a common stock (no ADRs of microcaps, no SPACs, no leveraged ETFs).

These thresholds are configurable in a single `config.yaml` file. They should be reviewed quarterly against the alerts they're producing — if too few alerts pass, loosen them; if too many low-quality names are getting through, tighten them.

### 5.3 Setup filter

The Kangaroo setup is a quality company that has dropped meaningfully but is not in a death spiral. Encoded as:

- Drawdown from 52-week high is between 8% and 30%. Below 8% there's no real "dip" yet; above 30% the thesis that this is a temporary setback gets weak fast.
- Current price is within 15% of the 200-day moving average, OR has been above the 200-day MA at any point in the last 60 trading days. This filters out names in confirmed long-term downtrends.
- The 14-day RSI is below 40. This is a soft oversold filter — looser than the textbook "RSI < 30" because Kangaroo entries don't need to be at peak panic.

### 5.4 Earnings blackout

Drop any ticker with a confirmed earnings release in the next 5 trading days. This is the single highest-leverage filter — buying a dip 2 days before earnings is one of the most reliable ways to compound bad news. No LLM analysis can rescue a ticker that is about to confirm the bad news the market is pricing in.

### 5.5 Sector check

For each surviving ticker, check whether its sector ETF (XLK, XLF, XLE, etc.) is also down by at least 1.5% on the day. If yes, flag the alert as `sector_wide` rather than dropping it outright — the user may still want to see it, but the framing is different. A sector-wide drop is a macro event, not an idiosyncratic Kangaroo setup.

### 5.6 News retrieval

For each remaining ticker, pull the last 24 hours of headlines and the full text of the top 3 most recent articles from a paid news API. Cache aggressively — the same article will appear for the same ticker across multiple pipeline runs in a day, and re-fetching it is wasteful.

### 5.7 Keyword blocklist

Run before any LLM. If any of the following terms appear in any retrieved headline or article body for the ticker, drop the candidate immediately and log the reason:

- `SEC investigation`, `SEC subpoena`, `DOJ investigation`, `DOJ subpoena`
- `restatement`, `material weakness`, `accounting irregularity`, `going concern`
- `Chapter 11`, `Chapter 7`, `bankruptcy filing`, `delisting notice`, `going private`
- `Ponzi`, `fraud`, `accounting fraud`
- `CFO resigned`, `CEO resigned`, `auditor resigned`, `auditor dismissed`
- `guidance withdrawn`, `withdraws guidance`, `suspends guidance`

This list is the most important safety mechanism in the entire system. It is checked into version control, reviewed before every release, and only added to (never silently removed from). Phrasing variants matter — match case-insensitively and check for both "CFO resigned" and "Chief Financial Officer has resigned." A small false positive rate here is fine; missing a real disqualifier is not.

## 6. Phased Rollout

Build in two phases. Phase 1 is a complete, useful product on its own. Phase 2 adds the LLM only after Phase 1 has proven its value.

### Phase 1: Screener + Notifications (no LLM)

Build the entire pipeline above through step 7 (keyword blocklist), plus persistence, notifications, and the dashboard. No LLM. The notification simply says: "AAPL down 6.2% on $X news. Quality + setup filters passed. Tap for evidence."

Run this for **at least two weeks**. The success criterion is: are the alerts the bot produces actually tickers the user would want to research? If the answer is "no, they're noise" or "no, I would have found these myself anyway," the LLM was never going to fix that. Tune the filters until the alerts feel useful.

This phase also produces the labeled dataset Phase 2 will need: each alert, in hindsight, can be labeled "transient" (recovered within 5-20 days) or "structural" (continued to decline or stayed flat).

### Phase 2: LLM ranking layer

Once Phase 1 is producing useful alerts, add the LLM as a **ranking layer**, not a gate. Every alert that passes Phase 1 is sent to the user; the LLM's job is to prioritize them — high, medium, low — and to write the 2-3 sentence "why this might be transient" summary that goes on the dashboard.

This is a much easier job for an 8B-parameter model than "make a load-bearing trading decision." It's a summarization and ranking task with a human reviewer downstream. If the LLM gets it wrong on a specific alert, the user sees the underlying evidence and overrides it. Over time, the labeled outcomes from Phase 1 can be used to evaluate whether the LLM's ranking is actually adding value.

The model choice is less important than the evaluation harness (Section 8). Start with whatever runs comfortably on the Mac Mini — Llama 3.1 8B at 4-bit is a reasonable default. Don't invest time in a larger model until the smaller one has been honestly evaluated.

## 7. The Alert Format

Every alert, both in the notification and on the dashboard, follows the same structure. Consistency matters more than completeness — the user should be able to scan an alert in 30 seconds and know whether to look closer.

**Notification (one line):**
> `[HIGH] NKE -7.4% — Q2 guidance held, China softness one-time. Earnings 38d. Tap for details.`

**Dashboard card:**

- **Ticker, company name, sector**
- **Today's move** (% change, dollar volume, vs. 20-day avg volume)
- **Drawdown context** (from 52-week high, vs. 200-day MA, RSI-14)
- **Earnings** (days to next earnings, last earnings result one-liner)
- **Top 3 headlines** with source, timestamp, and a one-line LLM summary (Phase 2) or just the headline (Phase 1)
- **Why this passed** — a list showing which filters the ticker cleared and any flags (e.g., `sector_wide`)
- **Why this might be transient** — 2-3 sentence LLM summary (Phase 2 only)
- **Quick links** — to Yahoo Finance, the company's investor relations page, and the SEC EDGAR filings page
- **A small price chart** showing the last 60 trading days with the drop highlighted

The dashboard card never tells the user what to do. No "buy" or "consider buying." The framing is always "here's what we found, here's the evidence."

## 8. Persistence and Attribution Logging

The SQLite database is what makes this system get better over time. Without it, the user has no way to know if the alerts are actually helping.

### Schema (sketch)

```sql
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    timestamp_utc TEXT NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    sector TEXT,
    -- snapshot at alert time
    price_at_alert REAL NOT NULL,
    pct_change_day REAL,
    drawdown_from_52w_high REAL,
    rsi_14 REAL,
    pct_above_200dma REAL,
    days_to_next_earnings INTEGER,
    market_cap REAL,
    -- pipeline metadata
    flags TEXT,                  -- JSON array, e.g. ["sector_wide"]
    llm_priority TEXT,           -- 'high' | 'medium' | 'low' | NULL in Phase 1
    llm_score REAL,              -- 0-100 in Phase 2, NULL in Phase 1
    llm_summary TEXT,            -- Phase 2 only
    -- realized outcomes (filled in by nightly job)
    price_1d REAL,
    price_3d REAL,
    price_5d REAL,
    price_20d REAL,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_20d REAL,
    -- user action (filled in manually or via dashboard button)
    user_traded INTEGER,         -- 0 or 1
    user_notes TEXT,
    hindsight_label TEXT         -- 'transient' | 'structural' | 'unclear'
);

CREATE TABLE alert_evidence (
    alert_id INTEGER REFERENCES alerts(id),
    headline TEXT,
    source TEXT,
    url TEXT,
    published_utc TEXT,
    full_text TEXT
);

CREATE TABLE filtered_out (
    timestamp_utc TEXT NOT NULL,
    ticker TEXT NOT NULL,
    pct_change_day REAL,
    filter_name TEXT NOT NULL,   -- which filter dropped it
    filter_reason TEXT           -- e.g. "earnings in 3 days", "blocklist: 'going concern'"
);
```

The `filtered_out` table is essential and easy to skip. Reviewing why the bot **didn't** alert on a ticker is at least as valuable as reviewing the alerts themselves. If a ticker would have been a great Kangaroo trade and the bot dropped it at the quality filter, that's a tunable parameter. If it dropped it at the keyword blocklist, that's working as intended.

### Nightly job

A small script that runs at 5pm ET each weekday:

1. For every alert from N=1, 3, 5, and 20 trading days ago, fetch the closing price and update the `price_*` and `return_*` columns.
2. Compute aggregate stats: hit rate (% of alerts with positive 5-day return), average return by `llm_priority` bucket, average return broken out by which filters the ticker cleared, etc.
3. Render these to a "Performance" tab on the dashboard.

After 2-3 months of data, this is what tells the user whether the system is helping. If high-priority alerts have a noticeably better 5-day return than medium-priority alerts, the LLM is adding value. If not, the LLM is theater and can be removed without loss.

## 9. Backtesting Approach

Backtesting is a separate, occasional activity, not part of the live system.

When tuning filter parameters (the quality thresholds, the RSI cutoff, the drawdown range), use `vectorbt` on the RTX 3060 Ti PC against 3-5 years of historical data. The goal is not to "validate the strategy" — there's no autonomous strategy to validate — but to answer specific questions like:

- If I tightened the market cap floor from $10B to $20B, how much would my alert volume drop, and would the average alert quality improve?
- Across the last 5 years of alerts the current filters would have produced, what was the average 5-day forward return? The 20-day?
- How does that average return compare to just buying SPY on the same days?

Use realistic friction assumptions even though the user trades manually: 0.1% slippage and a $0 commission (Alpaca, Schwab, Fidelity) or $1 per trade (IBKR). The user is human and will sometimes get worse fills than the close, but not dramatically worse on liquid large-caps.

This is not a system that needs walk-forward optimization, regime detection, or a Sharpe ratio above some threshold to "go live." It goes live as soon as the user finds the alerts useful. Backtesting is just a way to tune the dials.

## 10. Security

Less to defend here than in the original design, because there is no broker connection. Still:

- All API keys (market data, news, Pushbullet/Telegram) live in `.env` and are loaded at startup. `.env` is in `.gitignore`. The repository contains an `.env.example` with the variable names but no values.
- The dashboard binds to `127.0.0.1` only by default. If accessed from a phone on the home network, it's reached through Tailscale or a similar zero-config VPN, not exposed to the public internet.
- The SQLite database file is backed up nightly to a separate location (Time Machine, an external drive, or a private cloud bucket). Losing the alert history loses the ability to evaluate the system.
- No PII is stored. No broker credentials are stored, ever.

## 11. Project Layout

A single Python project, no monorepo:

```
kangaroo/
├── .env.example
├── .gitignore
├── config.yaml             # all tunable thresholds
├── pyproject.toml
├── README.md
├── src/
│   └── kangaroo/
│       ├── __init__.py
│       ├── config.py       # loads config.yaml + .env
│       ├── pipeline.py     # the filter pipeline orchestration
│       ├── filters/
│       │   ├── universe.py
│       │   ├── quality.py
│       │   ├── setup.py
│       │   ├── earnings.py
│       │   ├── sector.py
│       │   └── blocklist.py
│       ├── sources/
│       │   ├── market_data.py
│       │   ├── news.py
│       │   └── earnings_calendar.py
│       ├── llm/            # Phase 2 only
│       │   ├── classifier.py
│       │   └── prompts.py
│       ├── db/
│       │   ├── schema.sql
│       │   └── repository.py
│       ├── dashboard/
│       │   ├── app.py      # FastAPI
│       │   └── templates/
│       ├── notify.py
│       └── jobs/
│           ├── pipeline_run.py    # the every-30-min job
│           └── nightly.py         # the 5pm ET job
└── tests/
    ├── test_filters.py
    ├── test_blocklist.py
    └── test_repository.py
```

Tests focus on the filters and the blocklist — the pieces where a silent bug would matter most. The LLM classifier, when added, gets its own labeled-eval test suite using a held-out set of historical news events.

## 12. Re-alerting and Ladder Tracking

The user's actual trading style is laddered entry: if a Kangaroo candidate drops further after the first alert, that's a signal for a second (lower-cost) entry, not a reason to suppress the alert. The system needs to support this without becoming spammy on names that drift sideways or chop within a small range.

### The model: "watchlist with thresholds"

Once a ticker generates its first alert, it enters a **tracked state** for the next 10 trading days. While tracked, the ticker is re-evaluated on every pipeline run, but the criteria for re-alerting are different from the initial alert criteria. The first alert says "this is a Kangaroo setup worth your attention." A re-alert says "this Kangaroo setup got cheaper by a meaningful amount — consider adding a rung."

The defining question for any re-alert rule is: *what counts as "meaningfully cheaper"?* Three things have to be true at once:

1. **The price has dropped a configurable step below the last alert price.** Default: 3%. So if the first alert fired at $100, the next re-alert threshold is $97, then $94.09, then $91.27, and so on. Each rung is 3% below the previous rung's *price*, not 3% below the original price — the steps compound, which matches how a ladder strategy actually scales in.
2. **No disqualifying news has appeared since the last alert.** The blocklist runs on every re-evaluation, not just the first. If a ticker drops further because the SEC just opened an investigation, the system must drop it from tracking and notify the user that the thesis is broken — not invite another rung.
3. **The original quality and setup conditions still hold.** Specifically: the ticker hasn't dropped through the 30%-from-52-week-high floor, hasn't crossed below the 200-day MA by more than 15%, and isn't suddenly within 5 trading days of an earnings release that wasn't on the calendar before.

If all three are true, fire a re-alert tagged as a **rung event** — labeled rung 2, rung 3, etc. — and update the tracked state with the new price.

### What stops the tracking

A tracked ticker exits tracking — and stops generating re-alerts — when any of the following happens:

- **Recovery.** Price closes more than 4% above the most recent alert price. The dip recovered; the ladder thesis is over. Log this as a `recovered` exit.
- **Time expiry.** 10 trading days have passed since the original alert with no re-alert. The setup is stale. Log this as `expired`.
- **Thesis broken.** The blocklist hits, or the company drops through the structural-damage floor (down more than 30% from 52-week high, or more than 15% below the 200-day MA). Log this as `thesis_broken` and send a notification clearly framed as a warning, not a buying opportunity: "AAPL tracking ended — material new disclosure detected."
- **User action.** The user marks the ticker as "done" on the dashboard (e.g., they've taken their full intended position and don't want more rungs).

The tracking window is reset by a fresh cycle: if a ticker recovers, exits tracking, and then sets up again 3 weeks later with a new initial alert, that starts a new ladder from rung 1.

### What this prevents

The default rules above are deliberately tuned to prevent the two failure modes that wreck this kind of system:

**Spam on choppy names.** Without a meaningful step size, a stock oscillating between -6% and -8% all day would generate dozens of alerts. The 3% step plus the rule that each rung is measured against the *last* alert price means the stock has to keep making new lows by a real margin to keep alerting.

**Adding rungs into a falling knife.** The whole point of "is this still a Kangaroo, or is it now structural damage" is to detect when a normal dip has become something else. The thesis-broken check is non-negotiable on every re-evaluation. A ladder strategy is great when the dip recovers; it is catastrophic when you keep buying a name that has fundamentally changed. The system errs on the side of cutting tracking off too early rather than too late.

### Configuration

These belong in `config.yaml` so they can be tuned without code changes:

```yaml
ladder:
  step_pct: 3.0              # how much further down before re-alerting
  max_rungs: 5               # cap on alerts per tracked ticker
  tracking_window_days: 10   # how long a ticker stays tracked
  recovery_exit_pct: 4.0     # % above last alert that ends tracking
  structural_damage_drawdown_pct: 30.0  # absolute floor from 52w high
  structural_damage_below_200dma_pct: 15.0
```

Five rungs is a reasonable cap — past five rungs in a 10-day window the stock is down roughly 14% from the original alert, which is well into "this might be structural" territory and worth a manual review rather than another automated nudge.

### Schema changes

Two additions to the database. First, a new table for tracked tickers:

```sql
CREATE TABLE tracked_tickers (
    ticker TEXT PRIMARY KEY,
    first_alert_id INTEGER REFERENCES alerts(id),
    first_alert_timestamp_utc TEXT NOT NULL,
    first_alert_price REAL NOT NULL,
    last_alert_id INTEGER REFERENCES alerts(id),
    last_alert_timestamp_utc TEXT NOT NULL,
    last_alert_price REAL NOT NULL,
    rung_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,          -- 'active' | 'recovered' | 'expired' | 'thesis_broken' | 'user_closed'
    closed_timestamp_utc TEXT,
    closed_reason TEXT
);
```

Second, two columns on the `alerts` table:

```sql
ALTER TABLE alerts ADD COLUMN rung_number INTEGER NOT NULL DEFAULT 1;
ALTER TABLE alerts ADD COLUMN parent_alert_id INTEGER REFERENCES alerts(id);
```

`rung_number` is 1 for the initial alert, 2 for the first re-alert, etc. `parent_alert_id` points back to the initial alert for the ladder, which makes it trivial to query "show me all the rungs of the AAPL ladder that started on March 12."

### Pipeline changes

The pipeline grows a branch at the start of each run:

1. **Tracked-ticker re-evaluation.** Pull every row from `tracked_tickers` where `status = 'active'`. For each, fetch the current price and re-run: blocklist check (against news from the last 24 hours), structural damage check, earnings blackout, and the step-threshold check. Take whichever action applies — fire a rung re-alert, close out tracking with `thesis_broken`, close out with `recovered`, or do nothing if the price is somewhere between the last alert and the next rung threshold.

2. **Universe scan for new alerts.** Same as before, but with one new filter: if a ticker is already in `tracked_tickers` with `status = 'active'`, route it through the re-evaluation path rather than the new-alert path. This prevents the system from generating a "new" alert for a ticker that's already on a ladder.

The end-of-day nightly job also runs a sweep: any tracked ticker that hasn't had activity in `tracking_window_days` gets its status flipped to `expired`.

### Notification format for rungs

Rung re-alerts use a visibly different format from initial alerts so the user can distinguish them at a glance:

- **Initial alert:** `[NEW] NKE -7.4% — Q2 guidance held. Tap for details.`
- **Rung re-alert:** `[RUNG 2] NKE -3.1% from rung 1 ($92.40). Total drawdown 10.3%. No new disqualifying news. Tap for details.`
- **Thesis-broken close:** `[CLOSED] NKE tracking ended. Reason: 'guidance withdrawn' detected in 8:47am headline. Review before any further action.`

The rung notification always includes the cumulative drawdown from the original alert price, because that's the number that actually matters when deciding whether to add a rung. Rung 4 is much more interesting than rung 2 even though the *step* between rungs is the same.

### Dashboard changes

The dashboard grows a "Ladders" tab showing all currently active tracked tickers, each as a card showing:

- Ticker, original alert date, current rung count
- Original alert price, last alert price, current price, cumulative drawdown
- A small price chart with each rung marked as a horizontal line
- The next rung's trigger price (calculated as last alert price × 0.97 by default), so the user can see how far the stock has to fall before the next alert
- A "close ladder" button that flips status to `user_closed` (e.g., the user has taken their full intended position)

Closed ladders move to a separate "Recent Ladders" view that retains the realized return columns for performance evaluation.

### Backtesting note

When you eventually backtest filter parameters (Section 9), the laddering behavior should be modeled too. The aggregate statistic to track isn't just "average return per alert" — it's "average return per ladder," weighting rungs equally (i.e., simulating a fixed dollar amount added at each rung). A ladder that fires 4 rungs and then recovers 8% has a meaningfully different P&L profile than a single alert that recovers 8%, and tuning the step size requires modeling that.

## 13. Build Order

In order, with rough size estimates:

1. **Skeleton + config + DB schema** — half a day. Project scaffolding, `.env` loading, SQLite init. Schema already includes the `tracked_tickers` table and the `rung_number`/`parent_alert_id` columns from day one — retrofitting them later is painful.
2. **Market data source + universe scan** — half a day. Verify the API works and the daily decliner list looks right.
3. **Quality + setup + earnings filters** — one day. Run them against today's market and eyeball the survivors.
4. **News retrieval + caching + blocklist** — one day. Test the blocklist against a hand-curated list of historical fraud disclosures to confirm it catches them.
5. **Persistence + filtered-out logging** — half a day.
6. **Initial alert generation (rung 1 only)** — half a day. Skip the laddering logic for now; just write each new candidate as `rung_number = 1` and add it to `tracked_tickers`.
7. **Notifications (Pushbullet or Telegram)** — half a day. Initial alert format only at this point.
8. **Tracked-ticker re-evaluation + ladder logic** — one day. Implement the re-evaluation branch of the pipeline, the rung threshold check, the thesis-broken handling, and the rung re-alert notification format.
9. **Dashboard (FastAPI + Jinja templates)** — one to one-and-a-half days, including the Ladders tab.
10. **Scheduler + nightly job** — half a day, including the expired-ladder sweep.
11. **Run for two weeks. Tune. This is Phase 1 done.**
12. **(Phase 2)** LLM integration + labeled eval set + ranking display — one to two weeks, after Phase 1 has been validated.

Total Phase 1 build: roughly one focused week of working time, plus the two-week observation period.

## 14. What Success Looks Like

After three months of running:

- The user is checking the dashboard 1-2 times per trading day and acting on a small fraction of alerts.
- The labeled outcome data shows that alerts the user actually traded outperformed alerts the user skipped (i.e., the user's discretionary judgment is adding value on top of the bot's filtering).
- The blocklist has caught at least one disqualifier the user might have missed.
- The user has a clearer, data-backed sense of whether the Kangaroo strategy is working for them — separate from the question of whether any individual trade worked.

If after three months the alerts are not useful, the answer is to retire the system or rebuild the filters from scratch — not to add more complexity. The single biggest failure mode of projects like this is mistaking activity for progress: adding an LLM, adding more data sources, adding more filters, when the underlying signal isn't there. Be willing to walk away.