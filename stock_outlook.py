"""Probabilistic 1-3 week stock outlooks from price history and options IV."""

from __future__ import annotations

from datetime import date
import math
import re

import numpy as np
import pandas as pd
import yfinance as yf


HORIZONS = (5, 10, 15)
FEATURES = ("momentum_5", "momentum_20", "sma_gap", "rsi", "volatility")


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,14}", symbol):
        raise ValueError("Enter a valid ticker symbol, such as AAPL or BRK-B.")
    return symbol


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    relative = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + relative)


def feature_frame(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["Close"].astype(float)
    returns = close.pct_change()
    frame = pd.DataFrame(index=bars.index)
    frame["momentum_5"] = close.pct_change(5)
    frame["momentum_20"] = close.pct_change(20)
    frame["sma_gap"] = close / close.rolling(20).mean() - 1
    frame["rsi"] = (_rsi(close) - 50) / 25
    frame["volatility"] = returns.rolling(20).std() * math.sqrt(252)
    for horizon in HORIZONS:
        frame[f"forward_{horizon}"] = close.shift(-horizon) / close - 1
    return frame.replace([np.inf, -np.inf], np.nan)


def _historical_distribution(frame: pd.DataFrame, horizon: int) -> tuple[np.ndarray, float]:
    candidates = frame.dropna(subset=[*FEATURES, f"forward_{horizon}"])
    if len(candidates) < 80:
        raise ValueError("Not enough trading history to calculate a reliable outlook.")
    current = frame.iloc[-1][list(FEATURES)]
    if current.isna().any():
        raise ValueError("The latest market data is incomplete.")
    scale = candidates[list(FEATURES)].std().replace(0, 1)
    distances = (((candidates[list(FEATURES)] - current) / scale) ** 2).mean(axis=1) ** 0.5
    sample_size = min(120, max(60, len(candidates) // 5))
    nearest = distances.nsmallest(sample_size)
    outcomes = candidates.loc[nearest.index, f"forward_{horizon}"].to_numpy(dtype=float)
    similarity = float(1 / (1 + nearest.median()))
    return outcomes, similarity


def analyze_history(symbol: str, bars: pd.DataFrame) -> dict:
    if len(bars) < 260 or "Close" not in bars:
        raise ValueError("At least one year of daily price history is required.")
    bars = bars.dropna(subset=["Close"]).sort_index()
    frame = feature_frame(bars)
    close = bars["Close"].astype(float)
    spot = float(close.iloc[-1])
    latest = frame.iloc[-1]
    daily_vol = float(close.pct_change().tail(60).std())
    outlooks = []
    for horizon in HORIZONS:
        outcomes, similarity = _historical_distribution(frame, horizon)
        neutral_band = max(0.01, daily_vol * math.sqrt(horizon) * 0.30)
        bullish = float(np.mean(outcomes > neutral_band))
        bearish = float(np.mean(outcomes < -neutral_band))
        low, median, high = np.quantile(outcomes, [0.16, 0.5, 0.84])
        confidence = round(max(35, min(82, 42 + 38 * similarity)))
        outlooks.append({
            "trading_days": horizon,
            "weeks": horizon // 5,
            "bullish_probability": round(bullish, 4),
            "neutral_probability": round(1 - bullish - bearish, 4),
            "bearish_probability": round(bearish, 4),
            "range_low": round(spot * (1 + low), 2),
            "most_likely": round(spot * (1 + median), 2),
            "range_high": round(spot * (1 + high), 2),
            "confidence": confidence,
            "sample_size": len(outcomes),
        })

    score = float(latest["momentum_5"] * 2 + latest["momentum_20"] + latest["sma_gap"])
    bias = "Bullish" if score > 0.025 else "Bearish" if score < -0.025 else "Neutral"
    return {
        "symbol": symbol,
        "as_of": str(pd.Timestamp(bars.index[-1]).date()),
        "current_price": round(spot, 2),
        "bias": bias,
        "rsi": round(float(latest["rsi"] * 25 + 50), 1),
        "annualized_volatility": round(float(latest["volatility"]), 4),
        "momentum_5": round(float(latest["momentum_5"]), 4),
        "momentum_20": round(float(latest["momentum_20"]), 4),
        "support": round(float(close.tail(60).min()), 2),
        "resistance": round(float(close.tail(60).max()), 2),
        "outlooks": outlooks,
        "options_available": False,
    }


def _add_options_ranges(result: dict, ticker: yf.Ticker) -> None:
    expirations = list(ticker.options or [])
    if not expirations:
        return
    today = date.today()
    chains: dict[str, object] = {}
    added = False
    for item in result["outlooks"]:
        target_days = item["trading_days"] * 7 / 5
        expiry = min(expirations, key=lambda value: abs((date.fromisoformat(value) - today).days - target_days))
        days = max((date.fromisoformat(expiry) - today).days, 1)
        if expiry not in chains:
            chains[expiry] = ticker.option_chain(expiry)
        chain = chains[expiry]
        contracts = pd.concat([chain.calls, chain.puts], ignore_index=True)
        contracts = contracts.dropna(subset=["strike", "impliedVolatility"])
        contracts = contracts[contracts["impliedVolatility"].between(0.01, 5)]
        if contracts.empty:
            continue
        contracts = contracts.assign(distance=(contracts["strike"] - result["current_price"]).abs())
        iv = float(contracts.nsmallest(6, "distance")["impliedVolatility"].median())
        move = result["current_price"] * iv * math.sqrt(days / 365)
        item.update(options_expiry=expiry, implied_volatility=round(iv, 4),
                    options_range_low=round(result["current_price"] - move, 2),
                    options_range_high=round(result["current_price"] + move, 2))
        added = True
    result["options_available"] = added


def analyze_symbol(value: str) -> dict:
    symbol = normalize_symbol(value)
    ticker = yf.Ticker(symbol)
    bars = ticker.history(period="5y", interval="1d", auto_adjust=True)
    if bars.empty:
        raise ValueError(f"{symbol} was not found. Check the ticker symbol and try again.")
    result = analyze_history(symbol, bars)
    try:
        _add_options_ranges(result, ticker)
    except Exception:
        # Options data is supplemental; price-history analysis should still render.
        pass
    return result
