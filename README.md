# Kangaroo Research Assistant

A personal research and alerting tool for a single retail investor. Kangaroo scans US-listed equities for "Kangaroo" candidates — well-established companies that have dropped on potentially transient news — and sends a notification so you can research the situation and decide whether to act.

**Kangaroo never places trades.** It has no broker connection. You are the trader; Kangaroo is decision support.

---

## Table of Contents

- [How it works](#how-it-works)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running the system](#running-the-system)
- [The dashboard](#the-dashboard)
- [Understanding alerts](#understanding-alerts)
- [Ladder tracking](#ladder-tracking)
- [Notifications](#notifications)
- [The nightly job](#the-nightly-job)
- [Tuning the filters](#tuning-the-filters)
- [Development](#development)
- [Phase discipline](#phase-discipline)

---

## How it works

Every 30 minutes during US market hours, Kangaroo runs a filter pipeline:

```
Universe scan  →  Quality  →  Setup  →  Earnings  →  Sector  →  News  →  Blocklist  →  Alert
```

1. **Universe scan** — fetches today's biggest decliners from Polygon.io. Only tickers down at least 4% on at least 2× their 20-day average volume are considered.

2. **Quality filter** — drops small-caps, penny stocks, non-common-stock securities, and companies with negative TTM net income. Floor: $10B market cap, $50M average daily dollar volume.

3. **Setup filter** — looks for stocks in a "buyable" technical position: 8–30% off their 52-week high, within 15% of their 200-day moving average, and with RSI-14 below 40. Stocks in structural collapse (>30% off highs or deeply below the 200-day) are excluded.

4. **Earnings blackout** — drops any ticker with earnings within 5 trading days. Earnings volatility is not the same as Kangaroo volatility.

5. **Sector check** — if the sector ETF is also down ≥1.5% on the day, the alert is tagged `sector_wide`. The ticker is not dropped; the flag is there to inform your judgment.

6. **News retrieval** — fetches recent headlines and article text from Finnhub.

7. **Keyword blocklist** — drops any ticker whose recent news contains structural damage terms: SEC investigation, going concern, bankruptcy filing, fraud, guidance withdrawn, CFO resignation, and similar. This is the most important safety filter in the system. It is append-only.

8. **Alert** — surviving tickers are persisted to SQLite, a notification is sent, and the ticker enters the **ladder tracking** system.

---

## Setup

### Prerequisites

- Python 3.11 or later
- A [Polygon.io](https://polygon.io) API key (free tier is fine for delayed data)
- A [Finnhub](https://finnhub.io) API key (free tier)
- Either a [Pushbullet](https://www.pushbullet.com) access token or a [Telegram](https://core.telegram.org/bots) bot token + chat ID

### Install

```bash
git clone <repo-url> kangaroo
cd kangaroo

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

### Configure secrets

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

```dotenv
POLYGON_API_KEY=your_polygon_key
FINNHUB_API_KEY=your_finnhub_key

# Pushbullet (default):
PUSHBULLET_TOKEN=your_pushbullet_token

# Or Telegram (set provider: "telegram" in config.yaml):
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

DB_PATH=kangaroo.db
```

`.env` is gitignored. Never commit your keys.

### Initialize the database

```bash
python -m kangaroo.db.init
```

This creates `kangaroo.db` in the current directory (or at the path in `DB_PATH`). It is safe to run multiple times.

---

## Configuration

All tunable parameters live in `config.yaml`. Edit this file to change filter thresholds; do not modify filter source code directly.

```yaml
universe:
  min_pct_drop: 4.0          # minimum % decline to enter the pipeline
  min_relative_volume: 2.0   # minimum multiple of 20-day avg volume
  max_count: 100             # cap on candidates per run

quality:
  min_market_cap: 10_000_000_000       # $10B
  min_avg_daily_dollar_volume: 50_000_000  # $50M
  require_positive_ttm_income: true

setup:
  min_drawdown_pct: 8.0      # must be at least 8% off 52-week high
  max_drawdown_pct: 30.0     # no more than 30% off 52-week high
  max_pct_below_200dma: 15.0 # within 15% of 200-day MA
  max_rsi_14: 40.0           # RSI-14 below 40

earnings:
  blackout_days: 5           # trading days before earnings to block

sector:
  flag_threshold_pct: 1.5    # sector ETF drop % that triggers sector_wide flag

ladder:
  step_pct: 3.0              # % further drop to trigger next rung
  max_rungs: 5               # maximum re-alerts per ticker
  tracking_window_days: 10   # days a ticker stays active before expiring
  recovery_exit_pct: 4.0     # % above last alert price to close as recovered

notification:
  provider: "pushbullet"     # "pushbullet" or "telegram"
```

---

## Running the system

### Production (long-running scheduler)

```bash
python -m kangaroo.scheduler
```

This is the normal way to run Kangaroo. It starts APScheduler and runs:
- the **pipeline** every 30 minutes during market hours (9:30am–4:00pm ET, weekdays, US market holidays excluded)
- the **nightly job** once at 5:00pm ET on weekdays

Keep this process running continuously during market days (e.g. via `launchd` on macOS or a simple shell script).

### Dashboard

```bash
uvicorn kangaroo.dashboard.app:app --host 127.0.0.1 --port 8000
```

The dashboard is read-only (the only action it exposes is closing a ladder). It binds to `127.0.0.1` by default. To access it from your phone on the home network, use Tailscale or a similar zero-config VPN — do not expose port 8000 to the public internet.

### Manual pipeline run (testing / debugging)

```bash
python -m kangaroo.jobs.pipeline_run
```

Runs the pipeline once and exits. Useful for testing your API keys and configuration before starting the scheduler.

### Manual nightly job

```bash
python -m kangaroo.jobs.nightly
```

---

## The dashboard

Open `http://127.0.0.1:8000` in a browser.

### Today tab (`/`)

Shows every alert generated today, newest first. Each card displays:

| Field | Description |
|---|---|
| Ticker / company / sector | What it is |
| Price at alert | Price when the alert fired |
| Day change | % change on the day that triggered the alert |
| Drawdown from 52w high | How far off the 52-week high |
| RSI-14 | Momentum indicator at alert time |
| vs 200-day MA | % above or below the 200-day moving average |
| Days to earnings | Trading days until next earnings report |
| Flags | `sector_wide` if the whole sector was down too |
| Headlines | Up to 3 recent headlines that passed the blocklist |

The dashboard never tells you what to do. No "buy" or "consider buying."

### Ladders tab (`/ladders`)

Shows all currently active tracked tickers. For each ladder:

- Original alert date, current rung count, first/last alert price
- **Next rung trigger price** — the price level at which a rung-2+ re-alert would fire (last alert price × 0.97 by default)
- A **Close ladder** button — marks the ladder `user_closed` (e.g. you've taken your full intended position and don't want more rungs)

### Performance tab (`/performance`)

Aggregate stats computed by the nightly job: total alerts, hit rate (% with positive 5-day return), and average returns at 1, 3, 5, and 20 trading days. This view is only meaningful after several weeks of live data.

---

## Understanding alerts

An alert fires when a ticker passes every filter. It means:

- A large, profitable company dropped hard on elevated volume today
- The drop is in a "buyable" technical range (not a stock in long-term collapse)
- Earnings are not imminent
- Recent news contains no structural damage language

What it does **not** mean:

- That the thesis is correct — the drop may be structural even if the blocklist didn't catch it
- That you should buy — position sizing, timing, and context are entirely your decision
- That the price won't fall further — the ladder system handles that case

Always read the linked headlines. The alert is a starting point for research, not a conclusion.

---

## Ladder tracking

When a ticker alerts, it enters the **ladder tracking** system. Over the next `tracking_window_days` (default 10 trading days), Kangaroo monitors it on every pipeline run.

### Re-alert rungs

If the price drops another `step_pct` (default 3%) below the last alert price, a new rung fires: `[RUNG 2]`, `[RUNG 3]`, etc., up to `max_rungs` (default 5). Each rung notification includes the cumulative drawdown from the original alert.

Rung re-alerts use compounding steps: rung 2 triggers at −3% from rung 1, rung 3 at −3% from rung 2, and so on. A 5-rung ladder represents roughly a −14% move from the original alert price.

### Automatic closes

A ladder closes automatically under these conditions:

| Status | Condition |
|---|---|
| `recovered` | Price rises ≥4% above the last alert price |
| `thesis_broken` | New news hits the blocklist keyword list |
| `thesis_broken` | Stock drops >30% from its 52-week high, or >15% below the 200-day MA |
| `expired` | No activity within `tracking_window_days` (10 trading days) |

A `[CLOSED]` notification is sent immediately when a ladder closes on `thesis_broken`. Review the reason before taking any action — a blocklist hit is a hard stop signal.

### Manual close

Click **Close ladder** on the Ladders dashboard tab to mark a ladder `user_closed`. Use this when you've taken your full intended position and don't need further rungs.

---

## Notifications

Kangaroo sends three types of notifications:

| Prefix | When | Example |
|---|---|---|
| `[NEW]` | Rung-1 alert (first time a ticker alerts) | `[NEW] NKE -7.4% — Nike Inc. Tap for details.` |
| `[RUNG N]` | Rung-2+ re-alert | `[RUNG 2] NKE -3.1% from rung 1 ($92.40). Total drawdown 10.3%. Tap for details.` |
| `[CLOSED]` | Thesis-broken close | `[CLOSED] NKE tracking ended. Reason: guidance withdrawn detected. Review before any further action.` |

Notification failures are logged and swallowed — a send failure never aborts a pipeline run. If you miss a push notification, check the Today tab on the dashboard.

### Switching providers

In `config.yaml`:

```yaml
notification:
  provider: "telegram"   # or "pushbullet"
```

Add the corresponding keys to `.env`:

```dotenv
# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## The nightly job

Runs at 5:00pm ET each weekday. It does two things:

1. **Fills realized returns** — for every rung-1 alert from 1, 3, 5, and 20 trading days ago, fetches the current closing price and records the % return. This data populates the Performance tab over time.

2. **Expires stale ladders** — any active ladder whose first alert is older than `tracking_window_days` trading days is flipped to `expired`.

---

## Tuning the filters

All thresholds are in `config.yaml`. Common adjustments:

**Reduce alert volume:**
- Raise `min_market_cap` (e.g. $20B instead of $10B)
- Raise `min_pct_drop` (e.g. 5% instead of 4%)
- Lower `max_rsi_14` (e.g. 35 instead of 40) — requires more oversold

**Increase alert volume:**
- Lower `min_market_cap`
- Lower `min_pct_drop`
- Raise `max_rsi_14`

**Adjust ladder sensitivity:**
- Raise `step_pct` (e.g. 5%) to require a larger drop before re-alerting
- Lower `max_rungs` if you don't want to average down more than 2–3 times
- Lower `tracking_window_days` if you want faster expiry

**Earnings blackout window:**
- Raise `blackout_days` (e.g. 10) if you want a wider buffer around earnings

After 2–3 months of data, use the Performance tab to judge whether the current thresholds are producing useful alerts before changing them.

---

## Development

### Run tests

```bash
pytest                        # all tests
pytest tests/test_filters/    # filter tests only
pytest -k blocklist           # by keyword
```

### Lint and type-check

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy --strict src/kangaroo
```

A change is not done until all four commands pass cleanly.

### Project layout

```
kangaroo/
├── config.yaml                # all tunable thresholds
├── .env                       # secrets (gitignored)
├── .env.example               # template for secrets
└── src/kangaroo/
    ├── config.py              # settings loader
    ├── pipeline.py            # filter orchestration
    ├── notify.py              # push notifications
    ├── scheduler.py           # APScheduler process
    ├── filters/
    │   ├── universe.py        # decliner scan
    │   ├── quality.py         # fundamentals gate
    │   ├── setup.py           # RSI / drawdown / 200dma
    │   ├── earnings.py        # earnings blackout
    │   ├── sector.py          # sector-wide flag
    │   └── blocklist.py       # keyword safety filter (append-only)
    ├── sources/
    │   ├── market_data.py     # Polygon.io client
    │   └── news.py            # Finnhub client
    ├── db/
    │   ├── schema.sql         # SQLite schema
    │   └── repository.py      # all SQL
    ├── dashboard/
    │   ├── app.py             # FastAPI app
    │   └── templates/         # Jinja2 HTML
    └── jobs/
        ├── pipeline_run.py    # one-shot pipeline
        └── nightly.py        # returns + expiry sweep
```

### Adding a blocklist term

Open `src/kangaroo/filters/blocklist.py` and add the new term to `BLOCKLIST_TERMS`. The list is append-only — existing terms are never removed without explicit review. Run the blocklist tests after any change:

```bash
pytest tests/test_filters/test_blocklist.py -v
```

---

## Phase discipline

Kangaroo is built in two phases:

**Phase 1 (current)** — the screener, ladder logic, dashboard, and nightly attribution log. No LLM.

**Phase 2 (future)** — an LLM ranking and summarization layer using Ollama on the local Mac Mini. Phase 2 does not begin until Phase 1 has run for two weeks and produced data that is subjectively useful.

The `src/kangaroo/llm/` directory does not exist yet. Do not create it.

---

## What Kangaroo is not

- **Not a trading bot.** It cannot place, size, or route orders. It has no broker connection.
- **Not a portfolio manager.** Position sizing, stop losses, and exit decisions are yours.
- **Not a backtesting framework.** Historical parameter tuning is done separately with `vectorbt`.
- **Not financial advice.** An alert means a company passed a set of mechanical filters. It says nothing about whether the trade will work.
