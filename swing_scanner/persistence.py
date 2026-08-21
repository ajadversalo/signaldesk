"""Persistence for the scanner's selected next-session bullish predictions."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from database import connect

from . import config
from .strategy import ScanResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS swing_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    forecast_for TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    observed_price REAL NOT NULL,
    predicted_direction TEXT NOT NULL CHECK(predicted_direction IN ('UP', 'DOWN')),
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    momentum_pct REAL NOT NULL,
    acceleration REAL NOT NULL,
    relative_volume REAL NOT NULL,
    actual_price REAL,
    actual_direction TEXT CHECK(actual_direction IN ('UP', 'DOWN')),
    settled_at_utc TEXT,
    UNIQUE(symbol, observed_date)
)
"""

MODEL_VERSION = "swing-scanner-1.0"


def initialize(conn) -> None:
    conn.execute(SCHEMA)
    # A rank belongs to the first snapshot saved for that observation date.
    # This also protects against two app workers attempting the first save together.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_swing_predictions_daily_rank "
        "ON swing_predictions(observed_date, rank)"
    )
    conn.commit()


def next_business_day(date: str) -> str:
    """Return the next weekday; exchange-holiday support can be added later."""
    return str((pd.Timestamp(date) + pd.offsets.BDay(1)).date())


def save_predictions(results: list[ScanResult]) -> tuple[int, str | None]:
    """Save the top configured number of qualified calls once per observed date."""
    selected = [result for result in results if result.candidate][:config.MAX_RESULTS]
    if not selected:
        return 0, None
    observed_at = datetime.now(timezone.utc).isoformat()
    forecast_for = next_business_day(selected[0].date)
    conn = connect()
    saved = 0
    try:
        initialize(conn)
        existing = conn.execute(
            "SELECT COUNT(*), MIN(forecast_for) FROM swing_predictions WHERE observed_date = ?",
            (selected[0].date,),
        ).fetchone()
        if int(existing[0]) > 0:
            return int(existing[0]), existing[1]
        for rank, result in enumerate(selected, 1):
            cursor = conn.execute(
                """INSERT OR IGNORE INTO swing_predictions (
                       model_version, symbol, observed_date, forecast_for, observed_at_utc,
                       observed_price, predicted_direction, rank, score, momentum_pct,
                       acceleration, relative_volume
                   ) VALUES (?, ?, ?, ?, ?, ?, 'UP', ?, ?, ?, ?, ?)""",
                (MODEL_VERSION, result.symbol, result.date, forecast_for, observed_at,
                 result.close, rank, result.score, result.momentum_pct,
                 result.acceleration, result.relative_volume),
            )
            saved += max(int(cursor.rowcount), 0)
        conn.commit()
        return saved, forecast_for
    finally:
        conn.close()


def prediction_history(limit: int = 100) -> list[dict[str, object]]:
    """Return the newest saved swing calls for display."""
    conn = connect()
    try:
        initialize(conn)
        rows = conn.execute(
            """SELECT model_version, symbol, observed_date, forecast_for, observed_at_utc,
                      observed_price, predicted_direction, rank, score, momentum_pct,
                      acceleration, relative_volume, actual_price, actual_direction
                 FROM swing_predictions
                ORDER BY observed_date DESC, rank ASC, id ASC
                LIMIT ?""",
            (limit,),
        ).fetchall()
        columns = ["model_version", "symbol", "observed_date", "forecast_for",
                   "observed_at_utc", "observed_price", "predicted_direction", "rank",
                   "score", "momentum_pct", "acceleration", "relative_volume",
                   "actual_price", "actual_direction"]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()
