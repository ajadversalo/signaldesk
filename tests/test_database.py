from dataclasses import dataclass

import database


@dataclass
class StoredPrediction:
    model_version: str
    symbol: str
    market_data_symbol: str
    market_session_date: str
    previous_market_session_date: str
    forecast_for: str
    generated_at_utc: str
    current_price: float
    direction: str
    probability_up: float
    probability_down: float
    technical_probability_up: float
    momentum_direction: str
    news_sentiment: float
    validation_accuracy: float
    market_session_complete: bool = True


def prediction(session, previous, source, close, direction="UP"):
    return StoredPrediction(
        model_version="test", symbol="^XSP", market_data_symbol=source,
        market_session_date=session, previous_market_session_date=previous,
        forecast_for="2026-08-24", generated_at_utc=f"{session}T21:00:00+00:00",
        current_price=close, direction=direction, probability_up=0.6,
        probability_down=0.4, technical_probability_up=0.6,
        momentum_direction=direction, news_sentiment=0.0, validation_accuracy=0.5,
    )


def test_settlement_survives_market_data_fallback_change(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(tmp_path / "predictions.db"))

    assert database.record_prediction(
        prediction("2026-08-20", "2026-08-19", "^XSP", 771.0)
    )
    assert database.record_prediction(
        prediction("2026-08-21", "2026-08-20", "^GSPC", 6450.0, "DOWN")
    )

    stats = database.prediction_stats("test")
    settled = next(row for row in stats["recent"] if row["market_session_date"] == "2026-08-20")
    assert settled["actual_direction"] == "DOWN"
    # Prices from differently scaled proxies must not be compared or displayed as a return.
    assert settled["actual_close"] is None
    assert settled["return_percent"] is None


def test_stock_outlook_history_updates_same_session_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(tmp_path / "predictions.db"))
    result = {
        "symbol": "AAPL", "as_of": "2026-08-21", "current_price": 225.0,
        "bias": "Bullish", "outlooks": [{"weeks": 1, "range_low": 218.0,
                                            "range_high": 232.0}],
    }

    database.record_stock_outlook(result)
    database.record_stock_outlook({**result, "current_price": 226.0})
    history = database.stock_outlook_history()

    assert len(history) == 1
    assert history[0]["symbol"] == "AAPL"
    assert history[0]["current_price"] == 226.0
    assert history[0]["analyzed_at_utc"]

    assert database.delete_stock_outlook(history[0]["id"])
    assert database.stock_outlook_history() == []
    assert not database.delete_stock_outlook(history[0]["id"])
