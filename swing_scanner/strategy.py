"""Pure calculations for the momentum scan."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from . import config


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    date: str
    close: float
    momentum_pct: float
    previous_momentum_pct: float
    acceleration: float
    relative_volume: float
    pullback_pct: float
    score: float
    trend_ok: bool
    minimum_momentum_ok: bool
    momentum_improving: bool
    acceleration_ok: bool
    relative_volume_ok: bool
    pullback_ok: bool
    technical_signal: bool
    earnings_days: int | None = None
    earnings_ok: bool = True
    candidate: bool = False
    rejection_reasons: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate(symbol: str, bars: pd.DataFrame) -> ScanResult:
    """Evaluate one symbol from normalized lower-case OHLCV daily bars."""
    required = {"close", "volume"}
    if not required.issubset(bars.columns):
        raise ValueError(f"{symbol}: bars require close and volume columns")
    bars = bars.dropna(subset=["close", "volume"]).sort_index()
    if len(bars) < 201:
        raise ValueError(f"{symbol}: requires at least 201 daily bars, got {len(bars)}")

    close = bars["close"].astype(float)
    volume = bars["volume"].astype(float)
    latest = float(close.iloc[-1])
    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    sma200 = float(close.rolling(200).mean().iloc[-1])
    momentum = (latest / float(close.iloc[-21]) - 1) * 100
    previous_momentum = (float(close.iloc[-2]) / float(close.iloc[-22]) - 1) * 100
    acceleration = momentum - previous_momentum
    relative_volume = float(volume.iloc[-1] / volume.rolling(20).mean().iloc[-1])
    recent_high = float(close.iloc[-(config.PULLBACK_LOOKBACK_DAYS + 1):-1].max())
    pullback_pct = (recent_high - latest) / recent_high * 100

    checks = {
        "trend": latest > sma20 > sma50 > sma200,
        "minimum_momentum": momentum > config.MIN_MOMENTUM_PCT,
        "momentum_improving": momentum >= previous_momentum,
        "acceleration": acceleration > config.MIN_ACCELERATION,
        "relative_volume": relative_volume > config.MIN_RELATIVE_VOLUME,
        "pullback": pullback_pct <= config.MAX_PULLBACK_PCT,
    }
    required_checks = ["trend", "minimum_momentum", "momentum_improving",
                       "acceleration", "relative_volume"]
    if config.REQUIRE_PULLBACK:
        required_checks.append("pullback")
    technical_signal = all(checks[name] for name in required_checks)
    reasons = [name for name in required_checks if not checks[name]]

    return ScanResult(
        symbol=symbol, date=str(pd.Timestamp(bars.index[-1]).date()), close=round(latest, 4),
        momentum_pct=round(momentum, 4), previous_momentum_pct=round(previous_momentum, 4),
        acceleration=round(acceleration, 4), relative_volume=round(relative_volume, 4),
        pullback_pct=round(pullback_pct, 4),
        score=round(momentum + acceleration + relative_volume, 4),
        trend_ok=checks["trend"], minimum_momentum_ok=checks["minimum_momentum"],
        momentum_improving=checks["momentum_improving"],
        acceleration_ok=checks["acceleration"],
        relative_volume_ok=checks["relative_volume"], pullback_ok=checks["pullback"],
        technical_signal=technical_signal, rejection_reasons=",".join(reasons),
    )

