"""Phase 1: Database initialisation tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kangaroo.db.init import init_db


class TestDatabaseInit:
    def test_init_creates_all_tables(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert {"alerts", "alert_evidence", "filtered_out", "tracked_tickers"}.issubset(tables)

    def test_init_is_idempotent(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)
        init_db(db)  # must not raise

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "alerts" in tables

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        init_db(db)

        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.execute("PRAGMA foreign_keys")
        value = cursor.fetchone()[0]
        conn.close()

        assert value == 1
