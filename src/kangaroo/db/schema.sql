PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    sector TEXT,
    price_at_alert REAL NOT NULL,
    pct_change_day REAL,
    drawdown_from_52w_high REAL,
    rsi_14 REAL,
    pct_above_200dma REAL,
    days_to_next_earnings INTEGER,
    market_cap REAL,
    flags TEXT,
    llm_priority TEXT,
    llm_score REAL,
    llm_summary TEXT,
    price_1d REAL,
    price_3d REAL,
    price_5d REAL,
    price_20d REAL,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_20d REAL,
    user_traded INTEGER DEFAULT 0,
    user_notes TEXT,
    hindsight_label TEXT,
    rung_number INTEGER NOT NULL DEFAULT 1,
    parent_alert_id INTEGER REFERENCES alerts(id)
);

CREATE TABLE IF NOT EXISTS alert_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    headline TEXT,
    source TEXT,
    url TEXT,
    published_utc TEXT,
    full_text TEXT
);

CREATE TABLE IF NOT EXISTS filtered_out (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    ticker TEXT NOT NULL,
    pct_change_day REAL,
    filter_name TEXT NOT NULL,
    filter_reason TEXT
);

CREATE TABLE IF NOT EXISTS tracked_tickers (
    ticker TEXT PRIMARY KEY,
    first_alert_id INTEGER REFERENCES alerts(id),
    first_alert_timestamp_utc TEXT NOT NULL,
    first_alert_price REAL NOT NULL,
    last_alert_id INTEGER REFERENCES alerts(id),
    last_alert_timestamp_utc TEXT NOT NULL,
    last_alert_price REAL NOT NULL,
    rung_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    closed_timestamp_utc TEXT,
    closed_reason TEXT
);
