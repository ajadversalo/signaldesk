import numpy as np
import pandas as pd
import sqlite3
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from swing_scanner.persistence import (
    next_business_day,
    pending_prediction_symbols,
    prediction_history,
    save_predictions,
    settle_predictions,
)
from swing_scanner.strategy import evaluate


def test_strong_uptrend_passes_technical_filters():
    index = pd.bdate_range("2025-01-01", periods=240)
    returns = np.full(len(index), 0.001)
    returns[-22:] = 0.004
    returns[-1] = 0.01
    close = pd.Series(100 * np.cumprod(1 + returns), index=index)
    bars = pd.DataFrame({"close": close, "volume": 1_000_000.0}, index=index)
    bars.iloc[-1, bars.columns.get_loc("volume")] = 1_100_000

    result = evaluate("TEST", bars)

    assert result.trend_ok
    assert result.minimum_momentum_ok
    assert result.momentum_improving
    assert result.acceleration_ok
    assert result.relative_volume_ok
    assert result.technical_signal


def test_weak_momentum_does_not_pass():
    index = pd.bdate_range("2025-01-01", periods=240)
    close = pd.Series(np.linspace(100, 105, len(index)), index=index)
    bars = pd.DataFrame({"close": close, "volume": 1_000_000.0}, index=index)

    result = evaluate("TEST", bars)

    assert not result.minimum_momentum_ok
    assert not result.technical_signal


def test_top_predictions_are_saved_with_price_and_next_session(tmp_path, monkeypatch):
    index = pd.bdate_range("2025-01-01", periods=240)
    returns = np.full(len(index), 0.001)
    returns[-22:] = 0.004
    returns[-1] = 0.01
    close = pd.Series(100 * np.cumprod(1 + returns), index=index)
    bars = pd.DataFrame({"close": close, "volume": 1_000_000.0}, index=index)
    bars.iloc[-1, bars.columns.get_loc("volume")] = 1_100_000
    base = evaluate("ONE", bars)
    results = [replace(base, symbol=name, candidate=True, score=100 - rank)
               for rank, name in enumerate(["ONE", "TWO", "THREE", "FOUR"])]
    database_path = tmp_path / "predictions.db"
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(database_path))

    saved, forecast_for = save_predictions(results)

    assert saved == 3
    assert forecast_for == "2025-12-03"
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT symbol, observed_price, predicted_direction, rank, forecast_for "
            "FROM swing_predictions ORDER BY rank"
        ).fetchall()
    assert [row[0] for row in rows] == ["ONE", "TWO", "THREE"]
    assert all(row[1] == base.close and row[2] == "UP" for row in rows)
    assert [row[3] for row in rows] == [1, 2, 3]
    history = prediction_history()
    assert [row["symbol"] for row in history] == ["ONE", "TWO", "THREE"]
    assert history[0]["observed_price"] == base.close

    replacements = [replace(base, symbol=name, candidate=True, score=200 - rank)
                    for rank, name in enumerate(["FIVE", "SIX", "SEVEN"])]
    saved_again, forecast_again = save_predictions(replacements)
    unchanged = prediction_history()
    assert saved_again == 3
    assert forecast_again == forecast_for
    assert [row["symbol"] for row in unchanged] == ["ONE", "TWO", "THREE"]
    assert pending_prediction_symbols() == ["ONE", "THREE", "TWO"]

    settlement_bars = {
        symbol: pd.DataFrame(
            {"close": [base.close + 2]}, index=[pd.Timestamp(forecast_for)]
        ) for symbol in ["ONE", "TWO", "THREE"]
    }
    settled = settle_predictions(
        settlement_bars,
        now=datetime(2025, 12, 4, 9, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    settled_history = prediction_history()
    assert settled == 3
    assert all(row["actual_price"] == base.close + 2 for row in settled_history)
    assert all(row["actual_direction"] == "UP" for row in settled_history)
    assert pending_prediction_symbols() == []


def test_friday_forecast_settles_on_monday():
    assert next_business_day("2026-08-21") == "2026-08-24"
