import numpy as np
import pandas as pd

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
