import numpy as np
import pandas as pd
import pytest

from stock_outlook import analyze_history, normalize_symbol


def sample_bars(periods=900):
    rng = np.random.default_rng(14)
    returns = rng.normal(0.0007, 0.012, periods)
    close = 100 * np.cumprod(1 + returns)
    return pd.DataFrame({"Close": close, "Volume": 1_000_000},
                        index=pd.bdate_range("2022-01-03", periods=periods))


def test_outlook_has_three_probability_distributions():
    result = analyze_history("TEST", sample_bars())

    assert [row["trading_days"] for row in result["outlooks"]] == [5, 10, 15]
    for row in result["outlooks"]:
        total = (row["bullish_probability"] + row["neutral_probability"] +
                 row["bearish_probability"])
        assert total == pytest.approx(1, abs=0.0002)
        assert row["range_low"] < row["range_high"]
        assert 0 <= row["confidence"] <= 100


def test_symbol_validation():
    assert normalize_symbol(" brk-b ") == "BRK-B"
    with pytest.raises(ValueError):
        normalize_symbol("AAPL; DROP TABLE")
