# AGENTS.md — Kangaroo Research Assistant

This file is the persistent context for any AI coding agent (Claude Code, Cursor, etc.) working in this repository. Read this file in full before making any changes. If anything below is unclear or appears to conflict with a user request, **stop and ask** rather than guessing.

## What this project is

A **research and alerting tool** for a single retail investor. It scans US-listed equities for "Kangaroo" candidates — well-established companies that have dropped on potentially transient news — and notifies the user so they can research and place trades manually through their own broker.

**The system never places trades.** It has no broker connection, no order-routing code, and no portfolio management logic. The user is the trader; the system is decision support.

If a request appears to ask for autonomous trading, broker integration, order placement, or anything that would move money, **stop and confirm with the user before proceeding**. This is the single most important rule in this file.

## What this project is not

- Not an autonomous trading bot.
- Not a backtesting framework. (Backtesting is done ad-hoc with `vectorbt` outside this codebase.)
- Not a portfolio manager. Position sizing, stop losses, and exit decisions are the user's responsibility.
- Not a multi-user system. Single user, single process, runs locally.
- Not a low-latency system. Alerts fire on a 30-minute cadence; reaction time is measured in minutes, not milliseconds.

## Tech stack

- **Language:** Python 3.11+
- **Web framework:** FastAPI (for the local dashboard)
- **Templating:** Jinja2
- **Async runtime:** `asyncio` with `aiohttp` for outbound HTTP
- **Scheduling:** `apscheduler` (or a plain `asyncio` loop — either is acceptable)
- **Database:** SQLite via `aiosqlite` or stdlib `sqlite3`. No ORM. Hand-written SQL in `db/repository.py`.
- **Config:** `config.yaml` for tunable parameters; `.env` for secrets. Loaded with `pydantic-settings` or `python-dotenv` + manual parsing.
- **Market data and news APIs:** Polygon.io or Finnhub.io. Wrapper modules in `src/kangaroo/sources/` abstract the specific provider — strategy code does not call HTTP libraries directly.
- **Notifications:** Pushbullet or Telegram via their respective HTTP APIs.
- **Local LLM (Phase 2 only):** Ollama running on the same Mac Mini. Default model: `llama3.1:8b-instruct-q4_K_M`. Accessed via Ollama's HTTP API. Do not introduce a Phase 2 dependency until Phase 1 is shipped and validated.
- **Testing:** `pytest` with `pytest-asyncio`. No test framework other than pytest.
- **Lint/format:** `ruff` for both. No `black`, no `isort` — `ruff` covers both.
- **Type checking:** `mypy` in strict mode on the `src/kangaroo` package.

Do not introduce additional libraries without asking. In particular: do not add SQLAlchemy, Django, Celery, Redis, RabbitMQ, Kafka, FastStream, Pydantic v1 (we use v2), pandas (only `vectorbt` uses pandas, and that's outside this codebase), or any AI/ML framework beyond the Ollama HTTP client.

## Hardware context

- **Live system runs on:** Mac Mini M4 (the user's home machine). All scheduled jobs, the dashboard, and Ollama (in Phase 2) run here.
- **Research / backtesting runs on:** a separate RTX 3060 Ti PC. **No code in this repository should assume access to that machine, or to a GPU.** If a request involves GPU-accelerated work, that's a separate codebase and should be flagged.
- **Network:** the dashboard binds to `127.0.0.1` only. Remote access (from the user's phone) is via Tailscale or similar. Never bind to `0.0.0.0` or expose ports publicly.

## Repository layout

```
kangaroo/
├── .env.example
├── .gitignore
├── config.yaml
├── pyproject.toml
├── README.md
├── AGENTS.md                       # this file
├── src/
│   └── kangaroo/
│       ├── config.py               # config + secrets loading
│       ├── pipeline.py             # orchestration of filter pipeline
│       ├── filters/                # one filter per file, all pure functions
│       ├── sources/                # market data, news, earnings APIs
│       ├── llm/                    # Phase 2 only — do not create yet
│       ├── db/
│       │   ├── schema.sql
│       │   └── repository.py       # all SQL lives here
│       ├── dashboard/
│       │   ├── app.py
│       │   └── templates/
│       ├── notify.py
│       └── jobs/
│           ├── pipeline_run.py
│           └── nightly.py
└── tests/
```

New modules belong in one of the existing subpackages. Do not create new top-level packages without asking.

## Coding conventions

- **Type hints everywhere.** Every function signature has annotations. `mypy --strict` should pass.
- **Pure functions for filters.** Each filter in `src/kangaroo/filters/` takes data in and returns a pass/fail + reason. No I/O inside filter functions. I/O happens in `sources/` and the pipeline orchestrator passes data down.
- **All SQL lives in `db/repository.py`.** No SQL strings outside that module. The rest of the code calls repository functions.
- **No print statements in library code.** Use the stdlib `logging` module. Configure logging once at job entry points.
- **Async by default for I/O.** HTTP calls, database calls, and the FastAPI handlers are async. CPU-bound work (which there shouldn't be much of) can stay sync.
- **Configuration is data, not code.** Tunable thresholds (the quality floor, RSI cutoff, ladder step size, etc.) live in `config.yaml` and are loaded into a Pydantic settings model. Do not hardcode magic numbers in filter logic.
- **Errors don't crash the pipeline.** A failed news fetch for one ticker should log the error and skip that ticker, not abort the entire pipeline run. Wrap each ticker's processing in a try/except at the orchestrator level.
- **Time is UTC in the database, local in the dashboard.** All timestamps stored as ISO 8601 UTC. Dashboard rendering converts to America/New_York for display since the user trades US equities.

## Safety rules — never violate these

1. **No hardcoded API keys, ever.** Keys live in `.env`, loaded via `config.py`. `.env` is in `.gitignore`. The repo contains `.env.example` with variable names but no values. If you see a key in code during a review, that's a bug to fix, not a style issue to defer.
2. **No broker connections.** This codebase has no dependency on `alpaca-py`, `ib_insync`, `ccxt`, or any other broker SDK. If a task seems to require one, stop and confirm.
3. **No order placement code.** Even as a "stub" or "placeholder." A function called `place_order()` should not exist in this codebase, regardless of whether it's wired up.
4. **The keyword blocklist is append-only.** It lives in `src/kangaroo/filters/blocklist.py`. New terms can be added. Existing terms are never silently removed — if a removal is requested, confirm with the user and add a comment in the code explaining why.
5. **The `tracked_tickers` table is the source of truth for active ladders.** Do not introduce parallel state tracking (in-memory dicts, separate JSON files, etc.). If the database doesn't know a ticker is being tracked, the system doesn't know.
6. **The dashboard is read-only for trading data.** It can show alerts, ladders, and performance stats. It can let the user mark a ladder as "closed." It must not have any UI element that suggests placing, sizing, or executing a trade.
7. **No PII or credentials in logs.** Log ticker symbols, prices, filter outcomes. Never log API keys (even partially), Pushbullet/Telegram tokens, or anything from the user's environment beyond what's necessary.
8. **Never auto-add new tickers to the blocklist based on LLM output.** The blocklist is a hard rule list reviewed by a human. The LLM (Phase 2) advises on transient-vs-structural ranking; it does not modify the safety net.

## Build, test, and run commands

These should all work from the repo root with the virtualenv activated.

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in keys manually

# Database initialization (idempotent)
python -m kangaroo.db.init

# Run the pipeline once (manual / testing)
python -m kangaroo.jobs.pipeline_run

# Run the nightly attribution job
python -m kangaroo.jobs.nightly

# Start the dashboard
uvicorn kangaroo.dashboard.app:app --host 127.0.0.1 --port 8000

# Start the scheduler (the long-running production process)
python -m kangaroo.scheduler

# Tests
pytest                              # all tests
pytest tests/test_filters.py        # one file
pytest -k blocklist                 # by keyword

# Lint and type-check (run before any commit)
ruff check src/ tests/
ruff format --check src/ tests/
mypy --strict src/kangaroo
```

A change is not "done" until `ruff check`, `ruff format --check`, `mypy --strict`, and `pytest` all pass cleanly.

## Database conventions

- Schema lives in `src/kangaroo/db/schema.sql`. Migrations are not yet automated — for Phase 1, schema changes are applied by deleting the local DB and re-running `kangaroo.db.init`. This is acceptable because the user can rebuild from API replays. Once we have meaningful unrecoverable history (3+ months of alerts), we'll switch to Alembic. Do not introduce Alembic before then.
- All schema changes go in `schema.sql` and require a corresponding repository function update. Do not add columns ad-hoc from application code.
- The three tables that exist: `alerts`, `alert_evidence`, `filtered_out`, `tracked_tickers`. Do not add new tables without asking.
- Foreign keys are enforced (`PRAGMA foreign_keys = ON;` set on every connection).

## Testing expectations

Tests should cover:

- **Every filter in `src/kangaroo/filters/`.** Quality, setup, earnings, sector, and especially the blocklist. The blocklist tests should include a labeled set of ~20 historical fraud or distress disclosures (Wirecard, Luckin, FTX, Theranos-era, recent SEC enforcement actions) and assert that the blocklist catches them. This file is the single most important test in the repo.
- **The ladder logic.** A test that simulates a ticker generating rung 1, dropping further to trigger rung 2, then dropping into structural-damage territory and triggering a `thesis_broken` close. A separate test for recovery exit. A separate test for time expiry.
- **The repository layer.** Round-trip tests: write an alert, read it back, confirm fields match.
- **Pipeline integration.** A test that runs the pipeline with mocked sources and asserts the right alerts get persisted.

What does **not** need tests:

- The dashboard HTML rendering. Visual inspection is fine. We don't need Selenium.
- The scheduler itself. Trust `apscheduler`.
- The notification HTTP calls. Mock them; don't test against the live Pushbullet/Telegram APIs in CI.

## Phase discipline

The design has two phases. **Phase 1 must be shipped and run for two weeks before any Phase 2 work begins.**

- Phase 1 = the screener, the ladder logic, the dashboard, the attribution log. **No LLM.**
- Phase 2 = adding the LLM as a ranking and summarization layer.

If a task is requested that would mix Phase 2 work (Ollama integration, LLM evals, etc.) into Phase 1, stop and confirm. The phasing is deliberate — it's there to make sure the screener-only product is validated as useful before complexity gets added.

The `src/kangaroo/llm/` directory should not be created until Phase 2 starts.

## What to do when things are ambiguous

- **If a request might violate a safety rule above, stop and ask.** Do not "find a way to make it work." The safety rules exist precisely because they should be inconvenient to violate.
- **If a request would require a new top-level dependency, stop and ask.** The dependency list is intentionally short.
- **If a request would touch the blocklist, the schema, or the broker-related parts (which should not exist), stop and ask.**
- **If a filter threshold seems wrong, change `config.yaml`, not the filter code.** If the filter logic itself needs to change, that's a real code change and should be reviewed.
- **If you're about to write a `TODO` or `FIXME` comment, stop and ask whether the work should be done now.** The codebase is small enough that there's no excuse for accumulating debt.

## Out of scope — do not build these without explicit confirmation

- Trade execution, order routing, broker API integration of any kind.
- Position sizing logic, portfolio risk calculations, P&L tracking beyond the per-alert realized-return columns.
- Multi-user authentication or accounts.
- A public-facing web UI.
- Real-time WebSocket data feeds (the 30-minute polling cadence is sufficient).
- Crypto, options, futures, or any non-equity instrument.
- Tax-lot tracking, tax-loss harvesting, or any tax-related logic.
- Anything labeled "auto-trade," "smart execution," or "AI-powered investing."

If a task description uses any of those phrases, that's a strong signal to stop and confirm scope before writing code.