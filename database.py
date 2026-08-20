"""Prediction persistence for local SQLite or remote Turso/libSQL."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_symbol TEXT NOT NULL,
    market_data_symbol TEXT NOT NULL,
    market_session_date TEXT NOT NULL,
    forecast_for TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    observed_close REAL NOT NULL,
    predicted_direction TEXT NOT NULL CHECK(predicted_direction IN ('UP', 'DOWN')),
    probability_up REAL NOT NULL,
    probability_down REAL NOT NULL,
    technical_probability_up REAL NOT NULL,
    news_sentiment REAL NOT NULL,
    validation_accuracy REAL NOT NULL,
    actual_close REAL,
    actual_direction TEXT CHECK(actual_direction IN ('UP', 'DOWN')),
    settled_at_utc TEXT,
    UNIQUE(market_data_symbol, market_session_date)
)
"""


def connect():
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if url and token:
        import libsql
        return libsql.connect(database=url, auth_token=token)
    path = os.getenv("LOCAL_DATABASE_PATH", "data/predictions.db")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return sqlite3.connect(path, timeout=15)


def initialize(conn) -> None:
    conn.execute(SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_forecast ON predictions(market_data_symbol, forecast_for)")
    conn.commit()


def record_prediction(prediction) -> bool:
    """Settle yesterday, then preserve the first prediction observed per session."""
    p = asdict(prediction)
    conn = connect()
    try:
        initialize(conn)
        if p["market_session_complete"]:
            conn.execute(
                """UPDATE predictions
                   SET actual_close = ?,
                       actual_direction = CASE WHEN ? > observed_close THEN 'UP' ELSE 'DOWN' END,
                       settled_at_utc = ?
                   WHERE market_data_symbol = ? AND forecast_for = ? AND actual_direction IS NULL""",
                (p["current_price"], p["current_price"], p["generated_at_utc"],
                 p["market_data_symbol"], p["market_session_date"]),
            )
        cursor = conn.execute(
            """INSERT OR IGNORE INTO predictions (
                requested_symbol, market_data_symbol, market_session_date, forecast_for,
                observed_at_utc, observed_close, predicted_direction, probability_up,
                probability_down, technical_probability_up, news_sentiment,
                validation_accuracy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p["symbol"], p["market_data_symbol"], p["market_session_date"], p["forecast_for"],
             p["generated_at_utc"], p["current_price"], p["direction"], p["probability_up"],
             p["probability_down"], p["technical_probability_up"], p["news_sentiment"],
             p["validation_accuracy"]),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def prediction_stats() -> dict[str, Any]:
    conn = connect()
    try:
        initialize(conn)
        row = conn.execute(
            """SELECT COUNT(*),
                      COALESCE(SUM(CASE WHEN predicted_direction = actual_direction THEN 1 ELSE 0 END), 0),
                      COALESCE(AVG((probability_up - CASE WHEN actual_direction = 'UP' THEN 1.0 ELSE 0.0 END) *
                                   (probability_up - CASE WHEN actual_direction = 'UP' THEN 1.0 ELSE 0.0 END)), 0)
               FROM predictions WHERE actual_direction IS NOT NULL"""
        ).fetchone()
        pending = conn.execute("SELECT COUNT(*) FROM predictions WHERE actual_direction IS NULL").fetchone()[0]
        recent_rows = conn.execute(
            """SELECT market_session_date, forecast_for, observed_close, predicted_direction,
                      probability_up, actual_close, actual_direction
               FROM predictions ORDER BY market_session_date DESC LIMIT 20"""
        ).fetchall()
        settled, correct, brier = int(row[0]), int(row[1]), float(row[2])
        return {
            "settled": settled,
            "correct": correct,
            "accuracy": correct / settled if settled else None,
            "brier": brier if settled else None,
            "pending": int(pending),
            "recent": [enrich_row(values) for values in recent_rows],
            "backend": "Turso" if os.getenv("TURSO_DATABASE_URL") else "local SQLite",
        }
    finally:
        conn.close()


def enrich_row(values) -> dict[str, Any]:
    row = dict(zip(["market_session_date", "forecast_for", "observed_close",
                    "predicted_direction", "probability_up", "actual_close",
                    "actual_direction"], values))
    row["correct"] = (row["predicted_direction"] == row["actual_direction"]
                      if row["actual_direction"] else None)
    row["return_percent"] = (
        (float(row["actual_close"]) / float(row["observed_close"]) - 1) * 100
        if row["actual_close"] is not None else None
    )
    return row
