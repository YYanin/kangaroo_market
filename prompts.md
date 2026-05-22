# Kangaroo Research Assistant -- Implementation Prompts

High-level overview of each build phase, the implementation prompt an AI
agent should follow, the tests required to verify functionality, and the
final success criteria.

Read `AGENTS.md` and the design document
(`Kangaroo_Research_Assistant_Design.md`) before starting any phase.
The phases here mirror the build order in Section 13 of the design
document and must be completed in order.  Do not start a later phase
until the earlier one's tests pass.

---

## Phase 1 -- Project Skeleton, Config, and Database Schema

### Prompt

Set up the project scaffolding and the persistence layer.

- Create the directory structure described in `AGENTS.md` Section
  "Repository layout".  Empty `__init__.py` files where appropriate.
  Do **not** create `src/kangaroo/llm/` -- that is Phase 2 territory
  (post-Phase-1 validation).
- Create `pyproject.toml` with the dependencies listed in the
  `AGENTS.md` "Tech stack" section.  Pin to compatible release
  ranges (e.g. `aiohttp~=3.9`).  Add a `[project.optional-dependencies]
  dev` group containing `pytest`, `pytest-asyncio`, `ruff`, and `mypy`.
- Create `.env.example` listing the variable names: `POLYGON_API_KEY`,
  `FINNHUB_API_KEY`, `PUSHBULLET_TOKEN` (or `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID`).  No values.  Add `.env` to `.gitignore`.
- Create `config.yaml` with all tunable thresholds from the design
  document: quality filter floors, setup filter ranges, ladder step
  size, tracking window, etc.  Use the defaults specified in the
  design document.
- Create `src/kangaroo/config.py` exposing a `Settings` Pydantic v2
  model that loads `.env` and `config.yaml` together.  The settings
  object is the single entry point for any other module that needs
  a config value.
- Create `src/kangaroo/db/schema.sql` with the four tables described
  in the design document: `alerts`, `alert_evidence`, `filtered_out`,
  `tracked_tickers`.  Include the `rung_number` and `parent_alert_id`
  columns on `alerts` from day one.  Enable `PRAGMA foreign_keys = ON`.
- Create `src/kangaroo/db/repository.py` with placeholder async
  functions for the operations the pipeline will need: `insert_alert`,
  `insert_evidence`, `insert_filtered_out`, `get_active_tracked_tickers`,
  `upsert_tracked_ticker`, `close_tracked_ticker`.  Implementations
  can be stubs that raise `NotImplementedError` for now -- they will
  be filled in as later phases need them.
- Create `src/kangaroo/db/init.py` as a runnable module
  (`python -m kangaroo.db.init`) that creates the SQLite file at the
  path in config and applies `schema.sql`.  Idempotent (safe to run
  twice).

### Test

**tests/test_config.py :: TestConfigLoading**

- `test_config_loads_from_yaml`:
  Write a temp `config.yaml`, point the loader at it, assert the
  resulting `Settings` object has the expected values.

- `test_config_missing_required_env_var_raises`:
  Clear the env, attempt to load settings, assert a clear error is
  raised naming the missing variable.

**tests/test_db_init.py :: TestDatabaseInit**

- `test_init_creates_all_tables`:
  Run the init against a temp file, query
  `sqlite_master WHERE type='table'`, assert all four tables exist.

- `test_init_is_idempotent`:
  Run init twice against the same file.  Assert no error and the
  schema matches.

- `test_foreign_keys_enabled`:
  Open a connection through the repository module, query
  `PRAGMA foreign_keys`, assert it returns 1.

---

## Phase 2 -- Market Data Source and Universe Scan

### Prompt

Implement the first stage of the pipeline: pulling the day's
biggest decliners.

- Create `src/kangaroo/sources/market_data.py` with an async
  `MarketDataClient` class.  The constructor takes an API key and an
  `aiohttp.ClientSession`.  Methods needed for this phase:
  `get_daily_decliners(min_pct_drop, min_relative_volume, limit)`
  returning a list of typed records (use a Pydantic model
  `DeclinerRecord` with ticker, pct_change_day, dollar_volume,
  relative_volume).
- The provider implementation can be Polygon or Finnhub.  Pick one
  and document the choice in a module docstring.  The class interface
  must not leak the choice -- callers do not import provider-specific
  types.
- All HTTP calls retry with exponential backoff on 5xx responses
  (3 retries max).  Network errors are logged and re-raised; the
  pipeline orchestrator decides what to do with them.
- Create `src/kangaroo/filters/universe.py` with a function
  `apply_universe_filter(declarers, settings)` that takes the raw
  decliner list and applies the threshold cuts (min pct drop, min
  relative volume, max count).  Pure function; no I/O.

### Test

**tests/test_market_data.py :: TestMarketDataClient**

- `test_get_decliners_parses_response`:
  Mock the HTTP response with a fixture containing a known JSON
  payload.  Assert the returned list matches the expected
  `DeclinerRecord` objects.

- `test_get_decliners_retries_on_5xx`:
  Mock the HTTP layer to return 500 twice then 200.  Assert the call
  succeeds and the underlying request was made 3 times.

- `test_get_decliners_raises_on_4xx`:
  Mock a 401 response.  Assert a clear error is raised; do not retry
  on auth failures.

**tests/test_filters/test_universe.py :: TestUniverseFilter**

- `test_universe_filter_drops_below_pct_threshold`:
  Pass a mixed list, assert tickers below the configured pct drop
  are removed.

- `test_universe_filter_drops_below_volume_threshold`:
  Same idea, for the relative volume threshold.

- `test_universe_filter_caps_at_max_count`:
  Pass a list larger than the max, assert the returned list is
  exactly the max length and contains the largest decliners.

---

## Phase 3 -- Quality, Setup, and Earnings Filters

### Prompt

Implement the next three filters.  These run on the survivors from
Phase 2 and require additional fundamentals and calendar data.

- Extend `MarketDataClient` with: `get_fundamentals(ticker)` returning
  market cap, TTM net income, 30-day average dollar volume, security
  type; `get_price_history(ticker, days)` returning OHLCV; and
  `get_earnings_calendar(ticker)` returning the next earnings date
  if known.
- Create `src/kangaroo/filters/quality.py` with
  `apply_quality_filter(ticker, fundamentals, settings)` returning a
  `FilterResult` (passed: bool, reason: str | None).  Reason is set
  on failure (e.g. `"market_cap_below_floor"`).
- Create `src/kangaroo/filters/setup.py` with
  `apply_setup_filter(ticker, price_history, settings)`.  Computes
  drawdown from 52-week high, distance from 200-day MA, and 14-day
  RSI.  RSI calculation lives in
  `src/kangaroo/filters/_indicators.py` and has its own unit tests.
- Create `src/kangaroo/filters/earnings.py` with
  `apply_earnings_blackout(ticker, next_earnings_date, today, settings)`.
  Drops the ticker if the next earnings is within
  `settings.earnings_blackout_days` trading days.  Trading-day math
  lives in a small `_calendar.py` helper -- do not pull in `pandas`
  or `pandas_market_calendars`; a simple weekday-aware counter that
  excludes US federal holidays from a hardcoded list is sufficient.

### Test

**tests/test_filters/test_quality.py :: TestQualityFilter**

- `test_passes_when_all_criteria_met`
- `test_fails_on_market_cap_floor`
- `test_fails_on_negative_ttm_earnings`
- `test_fails_on_low_average_dollar_volume`
- `test_fails_on_non_common_stock_security_type`

Each test asserts both the boolean and the reason string.

**tests/test_filters/test_setup.py :: TestSetupFilter**

- `test_passes_in_buyable_drawdown_range`
- `test_fails_when_drawdown_below_minimum`:
  e.g. only 4% off 52-week high.
- `test_fails_when_drawdown_above_maximum`:
  e.g. 45% off 52-week high (structural damage territory).
- `test_fails_when_well_below_200dma`
- `test_fails_when_rsi_above_threshold`

**tests/test_filters/test_indicators.py :: TestRSI**

- `test_rsi_known_value_set`:
  Hand-computed RSI against a fixture price series.  Match to 2dp.
- `test_rsi_returns_none_with_insufficient_data`:
  Period 14 with only 10 data points -- assert `None`.

**tests/test_filters/test_earnings.py :: TestEarningsBlackout**

- `test_passes_when_earnings_more_than_5_trading_days_out`
- `test_fails_when_earnings_within_window`
- `test_passes_when_no_earnings_date_known`:
  No upcoming earnings on the calendar -- the filter is a positive
  exclusion, so absence of data is a pass, not a fail.  Document this
  choice in the docstring.

---

## Phase 4 -- News Retrieval, Caching, and Keyword Blocklist

### Prompt

Implement the news layer and the most important safety filter in the
system.  The blocklist runs **before** any LLM is ever introduced.

- Create `src/kangaroo/sources/news.py` with an async `NewsClient`.
  Methods: `get_recent_headlines(ticker, hours)` returning a list of
  `Headline` records (source, title, url, published_utc); and
  `get_article_text(url)` returning the full text where available.
- Add an in-memory cache (a simple async-safe LRU; do not pull in
  `cachetools`) keyed on `(ticker, hour_bucket)` for headline lookups
  and on URL for article bodies.  Cache TTL: 30 minutes for headlines,
  4 hours for article bodies.
- Create `src/kangaroo/filters/blocklist.py`.  This module contains a
  module-level constant `BLOCKLIST_TERMS: tuple[str, ...]` populated
  with the full list from Section 5.7 of the design document.  Provide
  `apply_blocklist(ticker, headlines, articles)` returning a
  `FilterResult`.  Match case-insensitively.  The reason string must
  include the offending term and a short snippet of context (the 80
  characters surrounding the match) to make the attribution log
  useful.
- Add a module docstring to `blocklist.py` containing the rule from
  `AGENTS.md`: this list is append-only and never silently shortened.

### Test

**tests/test_news.py :: TestNewsClient**

- `test_headlines_parsed_correctly`
- `test_article_body_cached`:
  Call `get_article_text` twice with the same URL.  Assert the HTTP
  layer is hit exactly once.

**tests/test_filters/test_blocklist.py :: TestBlocklist**

This file is the single most important test in the repo.  It must
contain a `HISTORICAL_DISCLOSURES` fixture with at least 20 real
historical fraud or distress disclosures.  Each fixture entry has a
ticker, a date, and the text of the actual headline or filing
language that should have triggered the blocklist.  Suggested set:
Wirecard (June 2020 statement on missing cash), Luckin Coffee (April
2020 internal investigation disclosure), FTX (November 2022 Chapter
11 filing language), Enron (October 2001 restatement disclosure),
Theranos-era partner statements, plus several recent SEC enforcement
press releases.

- `test_blocklist_catches_all_historical_disclosures`:
  Iterate the fixture.  For each entry, assert the blocklist returns
  `passed=False`.  The test must include the ticker in the assertion
  message so a future failure clearly identifies which disclosure
  was missed.
- `test_blocklist_does_not_flag_benign_news`:
  A fixture set of 10 benign headlines (earnings beats, product
  launches, analyst upgrades).  Assert the blocklist returns
  `passed=True` for each.
- `test_blocklist_match_is_case_insensitive`
- `test_blocklist_reason_includes_term_and_context`

---

## Phase 5 -- Sector Check and Initial Alert Generation

### Prompt

Tie the filters together into the new-alert path.  Ladder logic
comes in Phase 6 -- this phase only handles rung-1 alerts.

- Add `MarketDataClient.get_sector_etf_change(sector)` returning the
  day's percent change for the sector's representative ETF
  (XLK / XLF / XLE / etc.).  Hardcode the sector→ETF mapping in
  `src/kangaroo/sources/_sector_map.py`.
- Create `src/kangaroo/filters/sector.py` with
  `apply_sector_check(ticker_sector, sector_change)`.  Unlike the
  other filters this one does not drop the ticker -- it returns a
  flag `"sector_wide"` when the sector ETF is also down >=1.5%.  The
  flag rides along with the alert into persistence.
- Create `src/kangaroo/pipeline.py` with `run_pipeline(settings, db,
  market_data, news)` orchestrating the full sequence: universe ->
  quality -> setup -> earnings -> sector -> news -> blocklist.
  Surviving tickers become alerts with `rung_number = 1`.  Drops at
  any stage are written to `filtered_out` with the responsible
  filter name and reason.  Each ticker is processed inside its own
  try/except -- a single failure must not abort the run.
- Implement the repository functions actually used by this phase:
  `insert_alert`, `insert_evidence`, `insert_filtered_out`,
  `upsert_tracked_ticker` (used to register the new ladder).

### Test

**tests/test_filters/test_sector.py :: TestSectorCheck**

- `test_no_flag_when_sector_etf_flat`
- `test_flag_when_sector_etf_down_meaningfully`
- `test_no_flag_when_sector_etf_up`

**tests/test_pipeline.py :: TestPipelineNewAlerts**

- `test_pipeline_writes_alert_for_clean_pass`:
  Mock all sources to return data that passes every filter.  Run
  the pipeline against an in-memory SQLite.  Assert one alert row
  exists with `rung_number=1` and one tracked_tickers row exists
  with `status='active'`.

- `test_pipeline_writes_filtered_out_at_correct_stage`:
  Mock data so the ticker fails the earnings filter.  Assert no
  alert row, and a `filtered_out` row with `filter_name='earnings'`.

- `test_pipeline_continues_after_per_ticker_error`:
  Two tickers pass universe filter; one source call raises an
  exception during enrichment of ticker A.  Assert ticker B still
  produces an alert and the pipeline run completes without raising.

- `test_pipeline_does_not_double_alert_active_ladder`:
  Pre-populate `tracked_tickers` with an active row for AAPL.  Run
  the pipeline with AAPL appearing again in the universe scan.
  Assert no new rung-1 alert is created (this ticker should have
  been routed to the re-evaluation path -- which Phase 6 will
  implement, but for now the new-alert path must skip it).

---

## Phase 6 -- Ladder Tracking and Re-Alert Logic

### Prompt

Implement the re-evaluation branch of the pipeline so existing
ladders generate rung-2+ alerts when stocks drop further.

- In `pipeline.py`, add `re_evaluate_tracked_tickers(settings, db,
  market_data, news)` that runs **before** the new-alert universe
  scan in `run_pipeline`.  For every active row in `tracked_tickers`:
  1. Fetch current price and recent news.
  2. Run the blocklist on the new news.  If it hits, close the
     ladder with `status='thesis_broken'` and emit a thesis-broken
     notification (Phase 7 wires the actual sending; for now
     persist a flag the notification layer will read).
  3. Check the structural damage thresholds (drawdown from 52-week
     high, distance below 200-day MA).  If breached, close with
     `status='thesis_broken'`.
  4. Check the recovery threshold.  If the price has recovered above
     `last_alert_price * (1 + recovery_exit_pct/100)`, close with
     `status='recovered'`.
  5. Check the rung threshold.  If the price is at or below
     `last_alert_price * (1 - step_pct/100)` and the rung count is
     below the cap, write a new alert row with the next
     `rung_number` and `parent_alert_id` set, and update the
     tracked_tickers row's `last_alert_price`, `last_alert_id`,
     `last_alert_timestamp_utc`, and `rung_count`.
  6. Otherwise, do nothing.
- Add a nightly sweep function `expire_stale_ladders(db, settings,
  now)` that flips active ladders older than `tracking_window_days`
  to `status='expired'`.  Wire it into the existing nightly job
  module (which gets created in Phase 8 -- for now, just expose the
  function).

### Test

**tests/test_pipeline.py :: TestLadderReEvaluation**

- `test_rung_2_fires_when_price_drops_step_below_rung_1`:
  Seed an active ladder at $100.  Run re-evaluation with current
  price $96.50.  Assert a new alert with `rung_number=2`,
  `parent_alert_id` pointing to the rung-1 alert, and the
  tracked_tickers row updated.

- `test_no_rung_when_price_above_threshold`:
  Same setup, current price $98.  Assert no new alert.

- `test_rung_step_compounds`:
  Seed a ladder where rung 2 fired at $97.  Run re-evaluation at
  $94.10.  Assert rung 3 fires (97 * 0.97 = 94.09).  Run again at
  $94.50; assert no rung 3.

- `test_max_rungs_cap_enforced`:
  Seed a ladder at rung 5 (the cap).  Run re-evaluation with a
  price well below the next theoretical step.  Assert no new alert.

- `test_recovery_closes_ladder`:
  Seed a ladder, run re-evaluation with a price 5% above the last
  alert.  Assert tracked_tickers row's status is `'recovered'`.

- `test_blocklist_hit_closes_ladder_as_thesis_broken`:
  Seed a ladder.  Run re-evaluation with news containing
  `"guidance withdrawn"`.  Assert tracked_tickers status is
  `'thesis_broken'` and no rung alert was written even if the
  price was below the next rung threshold.

- `test_structural_damage_closes_ladder`:
  Seed a ladder.  Run re-evaluation with current price 35% below
  52-week high.  Assert status is `'thesis_broken'`.

- `test_expire_stale_ladders_flips_status`:
  Seed an active ladder dated 11 trading days ago.  Run the sweep.
  Assert status is `'expired'`.

---

## Phase 7 -- Notifications

### Prompt

Add the notification layer that pushes alerts to the user's phone.

- Create `src/kangaroo/notify.py` with a `Notifier` protocol and one
  concrete implementation: `PushbulletNotifier` or
  `TelegramNotifier` (whichever the user has configured).  Reading
  config decides which one is instantiated.
- Three notification types, each with a distinct visible prefix:
  - `[NEW]` for rung-1 alerts.
  - `[RUNG N]` for rung-2+ alerts, including cumulative drawdown
    from the original alert price.
  - `[CLOSED]` for thesis-broken closes, framed as a warning.
- Notification payloads use the formats specified in Section 12 of
  the design document.  Format strings live in `notify.py` as
  module-level constants so they can be unit-tested independently
  of the HTTP layer.
- The pipeline writes notification intent to a small queue table
  (or an in-memory list within a single run) and the notifier
  drains it at the end of the run.  This means a notification
  failure cannot break a partially-completed pipeline run.
- HTTP failures when sending a notification are logged and
  swallowed.  The user can always check the dashboard if they
  miss a push.

### Test

**tests/test_notify.py :: TestNotificationFormatting**

- `test_new_alert_format_contains_required_fields`:
  Format a new-alert notification.  Assert the string contains
  ticker, percent change, and the `[NEW]` prefix.
- `test_rung_alert_format_includes_cumulative_drawdown`:
  Format a rung-3 notification where original price was $100 and
  current is $89.  Assert the string contains `[RUNG 3]` and
  `"11"` (the cumulative drawdown).
- `test_closed_alert_includes_reason`:
  Format a thesis-broken close.  Assert the string contains
  `[CLOSED]` and the offending blocklist term.

**tests/test_notify.py :: TestNotifierIntegration**

- `test_notifier_drain_calls_http_for_each_pending`:
  Patch the HTTP client.  Queue 3 notifications.  Drain.  Assert
  3 HTTP calls.
- `test_http_failure_does_not_raise`:
  Patch the HTTP client to raise.  Drain a queue with one
  notification.  Assert no exception propagates.

---

## Phase 8 -- Dashboard, Scheduler, and Nightly Job

### Prompt

Wire up the read-only local UI and the long-running processes.

- Create `src/kangaroo/dashboard/app.py` -- a FastAPI app bound to
  `127.0.0.1` only.  Three pages, each rendered with Jinja:
  1. **Today** -- alerts from today, ordered newest first.  Each
     card shows the fields specified in Section 7 of the design
     document.
  2. **Ladders** -- all currently active tracked tickers, with
     each ladder's rungs visible and the next rung's trigger price
     calculated and displayed.  Includes a "close ladder" button
     that POSTs to an endpoint flipping `status` to `'user_closed'`.
  3. **Performance** -- aggregate stats from the nightly job:
     hit rate, average return at 1d/3d/5d/20d, breakdown by
     filter combinations.
- The dashboard is **read-only with respect to trading**.  No UI
  element suggests placing, sizing, or executing a trade.  The only
  write action allowed from the UI is closing a ladder.
- Create `src/kangaroo/jobs/pipeline_run.py` as a runnable module
  invoking `run_pipeline` once.  Used by the scheduler and for
  manual testing.
- Create `src/kangaroo/jobs/nightly.py` as a runnable module that:
  1. For every alert from N=1, 3, 5, 20 trading days ago, fetches
     the closing price and updates the realized-return columns.
  2. Calls `expire_stale_ladders`.
  3. Recomputes the aggregate stats consumed by the Performance
     page.
- Create `src/kangaroo/scheduler.py` -- the long-running process.
  Uses `apscheduler` (or a plain async loop) to run
  `pipeline_run.py` every 30 minutes during US market hours
  (9:30am-4:00pm ET, weekdays excluding US holidays) and
  `nightly.py` once at 5:00pm ET on weekdays.

### Test

**tests/test_dashboard.py :: TestDashboardPages**

- `test_today_page_renders_with_no_alerts`:
  Empty DB.  GET `/`.  Assert 200 and the page contains an
  "no alerts today" empty state.
- `test_today_page_renders_with_seeded_alerts`:
  Seed two alerts.  Assert both ticker symbols appear in the
  rendered HTML.
- `test_ladders_page_shows_active_only`:
  Seed three ladders: one active, one recovered, one expired.
  Assert only the active one is on the ladders page.
- `test_close_ladder_endpoint_updates_status`:
  POST to the close endpoint for an active ladder.  Assert the
  `tracked_tickers` row's status is `'user_closed'`.
- `test_dashboard_binds_to_localhost_only`:
  Assert `app.py` configures uvicorn / the test client with
  `host="127.0.0.1"`.  This is a regression-prevention test.

**tests/test_jobs/test_nightly.py :: TestNightlyJob**

- `test_nightly_fills_realized_returns`:
  Seed an alert from 5 trading days ago.  Mock the price source
  to return a known price.  Run the nightly job.  Assert the
  `price_5d` and `return_5d` columns are populated.
- `test_nightly_calls_expire_stale_ladders`:
  Seed an 11-trading-day-old active ladder.  Run nightly.  Assert
  status is `'expired'`.

**tests/test_scheduler.py :: TestScheduler**

- `test_scheduler_does_not_run_outside_market_hours`:
  Patch the clock to 8:00pm ET.  Assert the pipeline job is not
  triggered.
- `test_scheduler_does_not_run_on_weekends`
- `test_scheduler_does_not_run_on_us_holidays`

---

## Success Criteria

After all Phase 1-8 phases are complete, the following must be true:

 1. [  ] `python -m kangaroo.db.init` creates the SQLite database
         with all four tables and is safely idempotent.
 2. [  ] `python -m kangaroo.jobs.pipeline_run` executes a full
         pipeline run end-to-end against live API keys, producing
         alerts and/or filtered_out rows without raising.
 3. [  ] The keyword blocklist correctly catches every entry in the
         historical-disclosures fixture.
 4. [  ] A ticker that drops on benign news, passes all filters, and
         meets the setup criteria produces exactly one rung-1 alert
         and a corresponding `tracked_tickers` row with
         `status='active'`.
 5. [  ] When the same ticker drops a configurable percentage further
         on a subsequent pipeline run, a rung-2 alert is generated
         with `parent_alert_id` linking to the original rung-1 alert.
 6. [  ] When a tracked ticker has new news matching the blocklist,
         the ladder closes with `status='thesis_broken'` and a
         clearly-marked warning notification is sent.
 7. [  ] Ladders close automatically on recovery, time expiry, or
         the user clicking "close ladder" in the dashboard.
 8. [  ] The dashboard runs at `http://127.0.0.1:8000`, displays the
         Today / Ladders / Performance pages, and contains no UI
         element that suggests placing or sizing a trade.
 9. [  ] The scheduler runs the pipeline every 30 minutes during US
         market hours and the nightly job at 5:00pm ET.
10. [  ] The nightly job fills realized-return columns for past
         alerts and expires stale ladders.
11. [  ] No API keys, broker credentials, or order-placement code
         exist anywhere in the repository.
12. [  ] `ruff check`, `ruff format --check`, `mypy --strict src/`,
         and `pytest` all pass cleanly.
13. [  ] After running for 14 consecutive trading days, the
         dashboard's Performance page shows aggregate stats based on
         actual data and the alerts produced are subjectively useful
         to the user (Phase 1 acceptance).  Only after this gate is
         passed should Phase 2 -- LLM ranking layer -- begin.