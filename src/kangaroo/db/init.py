"""Initialize the SQLite database. Safe to run multiple times (idempotent).

Usage:
    python -m kangaroo.db.init
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from kangaroo.config import get_settings

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db(db_path: str | None = None) -> None:
    path = db_path or get_settings().db_path
    schema = _SCHEMA_PATH.read_text()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(schema)
        conn.commit()
        logger.info("Database initialised at %s", path)
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
