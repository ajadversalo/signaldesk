"""Download data, apply filters, rank candidates, and write a report."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd
import yfinance as yf

from . import config
from .strategy import ScanResult, evaluate


def download_bars(symbols: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    raw = yf.download(symbols, period=period, auto_adjust=True, group_by="ticker",
                      threads=True, progress=False, timeout=30)
    if raw.empty:
        raise RuntimeError("Market-data download returned no rows")
    output: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            part = raw[symbol].copy() if isinstance(raw.columns, pd.MultiIndex) else raw.copy()
            part.columns = [str(column).lower() for column in part.columns]
            part = part.dropna(how="all")
            if not part.empty:
                output[symbol] = part
        except (KeyError, TypeError):
            continue
    return output


def load_earnings(path: Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    frame = pd.read_csv(path)
    normalized = {column.casefold(): column for column in frame.columns}
    if "ticker" not in normalized or "earningsdate" not in normalized:
        raise ValueError("Earnings CSV requires Ticker and EarningsDate columns")
    result = frame[[normalized["ticker"], normalized["earningsdate"]]].copy()
    result.columns = ["ticker", "earnings_date"]
    result["ticker"] = result["ticker"].astype(str).str.upper()
    result["earnings_date"] = pd.to_datetime(result["earnings_date"]).dt.normalize()
    return result


def earnings_status(symbol: str, earnings: pd.DataFrame | None,
                    today: pd.Timestamp) -> tuple[int | None, bool]:
    if earnings is None:
        return None, True
    dates = earnings.loc[(earnings["ticker"] == symbol) &
                         (earnings["earnings_date"] >= today), "earnings_date"]
    if dates.empty:
        return None, True
    days = int((dates.min() - today).days)
    return days, days > config.EARNINGS_BLACKOUT_DAYS


def scan(bars_by_symbol: dict[str, pd.DataFrame], earnings: pd.DataFrame | None = None,
         today: pd.Timestamp | None = None) -> tuple[list[ScanResult], dict[str, str]]:
    today = (today if today is not None else pd.Timestamp.today()).normalize()
    results: list[ScanResult] = []
    errors: dict[str, str] = {}
    for symbol in config.WATCHLIST:
        bars = bars_by_symbol.get(symbol)
        if bars is None:
            errors[symbol] = "no market data"
            continue
        try:
            result = evaluate(symbol, bars)
            days, earnings_ok = earnings_status(symbol, earnings, today)
            score_ok = result.score >= config.MINIMUM_SCORE
            candidate = result.technical_signal and earnings_ok and score_ok
            reasons = result.rejection_reasons.split(",") if result.rejection_reasons else []
            if not earnings_ok:
                reasons.append("earnings_blackout")
            if not score_ok:
                reasons.append("minimum_score")
            results.append(replace(result, earnings_days=days, earnings_ok=earnings_ok,
                                   candidate=candidate, rejection_reasons=",".join(reasons)))
        except Exception as exc:
            errors[symbol] = str(exc)
    results.sort(key=lambda item: item.score, reverse=True)
    return results, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone bullish-momentum stock scanner")
    parser.add_argument("--earnings-csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/swing_scan.csv"))
    parser.add_argument("--all", action="store_true", help="Print every evaluated symbol")
    args = parser.parse_args()

    results, errors = scan(download_bars(config.WATCHLIST), load_earnings(args.earnings_csv))
    rows = [result.to_dict() for result in results]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.output, index=False)
    displayed = results if args.all else [result for result in results if result.candidate][:config.MAX_RESULTS]

    print(f"Scanned {len(results)} of {len(config.WATCHLIST)} symbols; {len(errors)} errors")
    print(f"Saved full report to {args.output}")
    print("\nRank  Symbol   Score  Momentum  Accel  RVOL  Earnings")
    for rank, result in enumerate(displayed, 1):
        earnings_text = "unknown" if result.earnings_days is None else f"{result.earnings_days}d"
        print(f"{rank:>4}  {result.symbol:<6} {result.score:>7.2f} "
              f"{result.momentum_pct:>8.2f}% {result.acceleration:>6.2f} "
              f"{result.relative_volume:>5.2f} {earnings_text:>8}")
    if errors:
        print("\nErrors:")
        for symbol, message in errors.items():
            print(f"  {symbol}: {message}")


if __name__ == "__main__":
    main()

