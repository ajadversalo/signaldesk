"""Prediction persistence for local SQLite or remote Turso/libSQL."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict
from typing import Any

import requests


SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL,
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
    momentum_direction TEXT CHECK(momentum_direction IN ('UP', 'DOWN')),
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
        return TursoHttpConnection(url, token)
    path = os.getenv("LOCAL_DATABASE_PATH", "data/predictions.db")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return sqlite3.connect(path, timeout=15)


class TursoHttpCursor:
    def __init__(self, rows: list[tuple], rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class TursoHttpConnection:
    """Minimal sqlite-compatible adapter over Turso's stateless HTTP API."""

    def __init__(self, url: str, token: str):
        base = url.strip().rstrip("/")
        if base.startswith("libsql://"):
            base = "https://" + base[len("libsql://"):]
        self.endpoint = base + "/v2/pipeline"
        self.token = token.strip()

    @staticmethod
    def _encode(value):
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "integer", "value": "1" if value else "0"}
        if isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        if isinstance(value, float):
            return {"type": "float", "value": value}
        return {"type": "text", "value": str(value)}

    @staticmethod
    def _decode(value):
        kind = value.get("type")
        raw = value.get("value")
        if kind == "null":
            return None
        if kind == "integer":
            return int(raw)
        if kind == "float":
            return float(raw)
        return raw

    def execute(self, sql: str, params=()):
        payload = {"requests": [{"type": "execute", "stmt": {
            "sql": sql, "args": [self._encode(value) for value in params],
            "named_args": [], "want_rows": True,
        }}]}
        response = requests.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        item = body.get("results", [{}])[0]
        if item.get("type") == "error":
            error = item.get("error", {})
            raise RuntimeError(f"Turso query failed: {error.get('message', error)}")
        result = item.get("response", {}).get("result", {})
        rows = [tuple(self._decode(value) for value in row) for row in result.get("rows", [])]
        return TursoHttpCursor(rows, int(result.get("affected_row_count", 0)))

    def commit(self):
        # Each stateless HTTP execute is committed atomically by Turso.
        return None

    def close(self):
        return None


def initialize(conn) -> None:
    conn.execute(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}
    if "model_version" not in columns:
        # Preserve pre-versioning rows without mixing them into current metrics.
        conn.execute(
            "ALTER TABLE predictions ADD COLUMN model_version TEXT NOT NULL DEFAULT 'legacy'"
        )
    if "momentum_direction" not in columns:
        conn.execute(
            "ALTER TABLE predictions ADD COLUMN momentum_direction TEXT "
            "CHECK(momentum_direction IN ('UP', 'DOWN'))"
        )
    # Older versions keyed by the fallback data symbol, allowing ^XSP and
    # ^GSPC proxy calls for the same requested session to appear twice.
    # Keep native-source rows first, otherwise keep the earliest observation.
    conn.execute(
        """DELETE FROM predictions WHERE id IN (
               SELECT id FROM (
                   SELECT id,
                          ROW_NUMBER() OVER (
                              PARTITION BY requested_symbol, market_session_date
                              ORDER BY CASE WHEN market_data_symbol = requested_symbol THEN 0 ELSE 1 END, id
                          ) AS duplicate_number
                   FROM predictions
               ) WHERE duplicate_number > 1
           )"""
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_session ON predictions(requested_symbol, market_session_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_forecast ON predictions(market_data_symbol, forecast_for)")
    conn.commit()


def record_prediction(prediction) -> bool:
    """Settle yesterday, then save one prediction per completed session."""
    p = asdict(prediction)
    if not p["market_session_complete"]:
        return False
    conn = connect()
    try:
        initialize(conn)
        conn.execute(
            """UPDATE predictions
                   SET forecast_for = ?,
                       actual_close = ?,
                       actual_direction = CASE WHEN ? > observed_close THEN 'UP' ELSE 'DOWN' END,
                       settled_at_utc = ?
                   WHERE market_data_symbol = ?
                     AND market_session_date = ?
                     AND actual_direction IS NULL""",
            (p["market_session_date"], p["current_price"], p["current_price"],
             p["generated_at_utc"], p["market_data_symbol"],
             p["previous_market_session_date"]),
        )
        cursor = conn.execute(
            """INSERT OR IGNORE INTO predictions (
                model_version, requested_symbol, market_data_symbol, market_session_date, forecast_for,
                observed_at_utc, observed_close, predicted_direction, probability_up,
                probability_down, technical_probability_up, momentum_direction, news_sentiment,
                validation_accuracy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p["model_version"], p["symbol"], p["market_data_symbol"], p["market_session_date"], p["forecast_for"],
             p["generated_at_utc"], p["current_price"], p["direction"], p["probability_up"],
             p["probability_down"], p["technical_probability_up"], p["momentum_direction"], p["news_sentiment"],
             p["validation_accuracy"]),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def prediction_stats(model_version: str) -> dict[str, Any]:
    """Return a scorecard containing only predictions from one model version."""
    conn = connect()
    try:
        initialize(conn)
        row = conn.execute(
            """SELECT COUNT(*),
                      COALESCE(SUM(CASE WHEN predicted_direction = actual_direction THEN 1 ELSE 0 END), 0),
                      COALESCE(AVG((probability_up - CASE WHEN actual_direction = 'UP' THEN 1.0 ELSE 0.0 END) *
                                   (probability_up - CASE WHEN actual_direction = 'UP' THEN 1.0 ELSE 0.0 END)), 0),
                      AVG(CASE WHEN actual_direction = 'UP' THEN 1.0 ELSE 0.0 END),
                      AVG(CASE WHEN momentum_direction IS NULL THEN NULL
                               WHEN momentum_direction = actual_direction THEN 1.0 ELSE 0.0 END),
                      AVG(validation_accuracy)
               FROM predictions WHERE model_version = ? AND actual_direction IS NOT NULL""",
            (model_version,),
        ).fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE model_version = ? AND actual_direction IS NULL",
            (model_version,),
        ).fetchone()[0]
        # The scorecard is version-specific, but the accountability ledger must
        # survive model upgrades. Otherwise the prediction made before a version
        # bump disappears just when its next-session result becomes available.
        recent_rows = conn.execute(
            """SELECT model_version, market_session_date, forecast_for, observed_close, predicted_direction,
                      probability_up, actual_close, actual_direction
               FROM predictions
               ORDER BY market_session_date DESC LIMIT 20""",
        ).fetchall()
        settled, correct, brier = int(row[0]), int(row[1]), float(row[2])
        return {
            "model_version": model_version,
            "settled": settled,
            "correct": correct,
            "accuracy": correct / settled if settled else None,
            "brier": brier if settled else None,
            "baselines": {
                "always_up_accuracy": float(row[3]) if row[3] is not None else None,
                "momentum_accuracy": float(row[4]) if row[4] is not None else None,
                "fifty_fifty_accuracy": 0.5,
                "fifty_fifty_brier": 0.25,
                "walk_forward_accuracy": float(row[5]) if row[5] is not None else None,
            },
            "pending": int(pending),
            "recent": [enrich_row(values) for values in recent_rows],
            "backend": "Turso" if os.getenv("TURSO_DATABASE_URL") else "local SQLite",
        }
    finally:
        conn.close()


def enrich_row(values) -> dict[str, Any]:
    row = dict(zip(["model_version", "market_session_date", "forecast_for", "observed_close",
                    "predicted_direction", "probability_up", "actual_close",
                    "actual_direction"], values))
    row["correct"] = (row["predicted_direction"] == row["actual_direction"]
                      if row["actual_direction"] else None)
    row["return_percent"] = (
        (float(row["actual_close"]) / float(row["observed_close"]) - 1) * 100
        if row["actual_close"] is not None else None
    )
    return row
