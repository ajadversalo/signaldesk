"""Risk-aware cash-secured put screener.

The objective is to maximize premium income *after* enforcing an assignment-risk
budget.  Delta and Black-Scholes probability are estimates, not guarantees; a
short American-style equity option can be assigned at any time.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from math import exp, log, sqrt
from pathlib import Path
from statistics import NormalDist
import tempfile
import traceback
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yfinance as yf
from ib_insync import IB, Option

# Avoid failures when yfinance's default per-user SQLite cache is read-only.
YFINANCE_CACHE = Path(tempfile.gettempdir()) / "waverider_yfinance_cache"
YFINANCE_CACHE.mkdir(parents=True, exist_ok=True)
yf.set_tz_cache_location(str(YFINANCE_CACHE))


WATCHLIST = [
    "TMUS", "T", "VZ", "WEC", "AEE", "DUK", "SO", "EXC",
    "JPM", "BAC", "WFC", "PNC", "USB", "GS", "MS", "AXP", "SCHW",
    "COF", "RY", "TD", "BNS", "CM", "SOFI", "XLF",
    "ABBV", "JNJ", "MRK", "GILD", "PFE", "BMY", "XLV",
    "KO", "PEP", "PG", "COST", "WMT", "KR", "GIS", "HSY", "KHC",
    "CPB", "XLP", "HD", "LOW", "BBY", "TGT",
    "AAPL", "MSFT", "GOOGL", "AMZN", "ORCL", "CSCO", "IBM", "QCOM",
    "NFLX", "AMD", "SHOP", "CAT", "GE", "HON", "UPS", "FDX", "XLI",
    "XOM", "CVX", "COP", "EOG", "FANG", "ENB", "SU", "CNQ",
    "O", "VICI", "SPG", "NNN", "WPC", "SPY", "QQQ",
]

# Contract constraints. A 0.15 absolute delta is often interpreted as roughly a
# 15% market-implied chance of finishing ITM, but it is not a literal guarantee.
MIN_DTE = 14
MAX_DTE = 30
MIN_ABS_DELTA = 0.05
MAX_ABS_DELTA = 0.15
MAX_PROBABILITY_ITM = 0.18
MIN_OTM_PCT = 5.0
MAX_STOCK_PRICE = 100.0
MIN_OPEN_INTEREST = 100
MIN_OPTION_VOLUME = 10
MAX_SPREAD_PCT = 12.0
MIN_ANNUALIZED_RETURN_PCT = 4.0
MIN_UNDERLYING_AVG_VOLUME = 1_000_000

# Event/data policy. Unknown earnings are retained but penalized and clearly
# flagged. Set this to True if missing earnings data should reject equities.
EARNINGS_BUFFER_DAYS = 2
REJECT_UNKNOWN_EARNINGS = False

# Used only in the approximate Black-Scholes calculation. Keep configurable.
RISK_FREE_RATE = 0.04

# TWS paper-trading defaults used elsewhere in this repository. TWS or IB
# Gateway must be running with API access enabled. Use a unique client ID.
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497
IBKR_CLIENT_ID = 303
IBKR_MARKET_DATA_TYPE = 3  # delayed; use 1 only with live US options subscriptions
IBKR_SNAPSHOT_BATCH_SIZE = 40

OUTPUT_FILE = Path(__file__).with_name("csp_candidates_v3.csv")
NORMAL = NormalDist()


class MarketDataSessionConflict(RuntimeError):
    """IBKR rejected quotes because this username has another live session."""


def install_ibkr_error_tracking(ib: IB) -> None:
    """Record fatal market-data errors so snapshot calls can fail fast."""
    ib.market_data_session_conflict = None

    def remember_error(req_id, error_code, error_string, contract):
        if error_code == 10197:
            ib.market_data_session_conflict = (
                f"reqId {req_id}: {error_string}"
                + (f" ({getattr(contract, 'localSymbol', contract)})" if contract else "")
            )

    # Keep a reference because Event stores weak references for some callables.
    ib._market_data_error_handler = remember_error
    ib.errorEvent += remember_error


def number(value, default: float | None = None) -> float | None:
    """Convert scalar-like market data to a finite float."""
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def rounded(value, digits: int = 2):
    parsed = number(value)
    return None if parsed is None else round(parsed, digits)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def as_date(value) -> date | None:
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        if isinstance(parsed, pd.DatetimeIndex):
            return parsed[0].date() if len(parsed) else None
        return parsed.date()
    except (TypeError, ValueError, IndexError, AttributeError):
        return None


def find_dates(value) -> list[date]:
    """Recursively extract date-like values returned by yfinance."""
    found: list[date] = []
    if value is None:
        return found
    if isinstance(value, pd.DataFrame):
        for item in list(value.index) + value.to_numpy().ravel().tolist():
            parsed = as_date(item)
            if parsed:
                found.append(parsed)
        return found
    if isinstance(value, pd.Series):
        for item in list(value.index) + value.tolist():
            parsed = as_date(item)
            if parsed:
                found.append(parsed)
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            if "earning" in str(key).lower():
                found.extend(find_dates(item))
        return found
    if isinstance(value, (list, tuple, set, pd.Index, np.ndarray)):
        for item in value:
            parsed = as_date(item)
            if parsed:
                found.append(parsed)
        return found
    parsed = as_date(value)
    return [parsed] if parsed else []


def next_earnings_date(ticker: yf.Ticker) -> date | None:
    """Return the next reported earnings date across yfinance response shapes."""
    today = datetime.now().date()
    candidates: list[date] = []
    try:
        candidates.extend(find_dates(ticker.calendar))
    except Exception:
        pass
    if not candidates:
        try:
            candidates.extend(find_dates(ticker.get_earnings_dates(limit=4)))
        except Exception:
            pass
    future = sorted({item for item in candidates if item >= today})
    return future[0] if future else None


def put_risk_estimates(
    spot: float,
    strike: float,
    dte: int,
    implied_volatility: float,
    dividend_yield: float = 0.0,
) -> tuple[float | None, float | None]:
    """Approximate absolute put delta and risk-neutral probability of expiry ITM."""
    if min(spot, strike, dte, implied_volatility) <= 0:
        return None, None
    years = dte / 365.0
    sigma_root_t = implied_volatility * sqrt(years)
    if sigma_root_t <= 0:
        return None, None
    d1 = (
        log(spot / strike)
        + (RISK_FREE_RATE - dividend_yield + 0.5 * implied_volatility**2) * years
    ) / sigma_root_t
    d2 = d1 - sigma_root_t
    put_delta = exp(-dividend_yield * years) * (NORMAL.cdf(d1) - 1.0)
    probability_itm = NORMAL.cdf(-d2)
    return abs(put_delta), probability_itm


def indicators(history: pd.DataFrame) -> pd.DataFrame:
    result = history.copy()
    close = result["Close"]
    result["SMA20"] = close.rolling(20).mean()
    result["SMA50"] = close.rolling(50).mean()
    result["SMA200"] = close.rolling(200).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    result["MACD"] = ema12 - ema26
    result["MACD_SIGNAL"] = result["MACD"].ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    result["RSI"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    result["ROC20"] = close.pct_change(20) * 100
    result["AVG_VOL20"] = result["Volume"].rolling(20).mean()
    return result


def quality_score(row: pd.Series, previous: pd.Series, info: dict) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    price = number(row.get("Close"), 0.0)
    sma20 = number(row.get("SMA20"), price)
    sma50 = number(row.get("SMA50"), price)
    sma200 = number(row.get("SMA200"), price)

    tests = [
        (price > sma20, 8, "above SMA20"),
        (price > sma50, 10, "above SMA50"),
        (price > sma200, 15, "above SMA200"),
        (sma20 > sma50, 8, "SMA20 above SMA50"),
        (sma50 > sma200, 8, "SMA50 above SMA200"),
        (sma200 > number(previous.get("SMA200"), sma200), 6, "rising SMA200"),
        (number(row.get("MACD"), 0) > number(row.get("MACD_SIGNAL"), 0), 7, "bullish MACD"),
        (number(row.get("ROC20"), -1) > 0, 5, "positive 20-day momentum"),
        (45 <= number(row.get("RSI"), 0) <= 68, 5, "constructive RSI"),
        (number(info.get("freeCashflow"), 0) > 0, 8, "positive free cash flow"),
        (number(info.get("revenueGrowth"), -1) > 0, 5, "revenue growing"),
        (number(info.get("earningsGrowth"), -1) > 0, 5, "earnings growing"),
        (number(info.get("marketCap"), 0) >= 10_000_000_000, 5, "large cap"),
        (0 < number(info.get("beta"), 99) <= 1.5, 5, "moderate beta"),
    ]
    for passed, points, reason in tests:
        if passed:
            score += points
            reasons.append(reason)
    return min(score, 100.0), reasons


def valid_expirations(ticker: yf.Ticker) -> list[tuple[str, int]]:
    today = datetime.now().date()
    valid = []
    for expiry in ticker.options:
        parsed = datetime.strptime(expiry, "%Y-%m-%d").date()
        dte = (parsed - today).days
        if MIN_DTE <= dte <= MAX_DTE:
            valid.append((expiry, dte))
    return valid


def ibkr_put_quotes(
    ib: IB,
    symbol: str,
    expiry: str,
    strikes: list[float],
) -> dict[float, object]:
    """Request IBKR snapshots and return them keyed by strike."""
    contracts = [
        Option(symbol, expiry.replace("-", ""), strike, "P", "SMART", currency="USD")
        for strike in strikes
    ]
    qualified = []
    for start in range(0, len(contracts), IBKR_SNAPSHOT_BATCH_SIZE):
        batch = contracts[start : start + IBKR_SNAPSHOT_BATCH_SIZE]
        qualified.extend(ib.qualifyContracts(*batch))

    quotes: dict[float, object] = {}
    if not qualified:
        return quotes

    # Probe one contract before submitting a full batch. If this username has a
    # competing live session, IBKR otherwise emits error 10197 once for every
    # contract in the batch before reqTickers returns control to us.
    ib.market_data_session_conflict = None
    for snapshot in ib.reqTickers(qualified[0]):
        quotes[float(snapshot.contract.strike)] = snapshot
    conflict = getattr(ib, "market_data_session_conflict", None)
    if conflict:
        raise MarketDataSessionConflict(conflict)

    for start in range(1, len(qualified), IBKR_SNAPSHOT_BATCH_SIZE):
        batch = qualified[start : start + IBKR_SNAPSHOT_BATCH_SIZE]
        for snapshot in ib.reqTickers(*batch):
            quotes[float(snapshot.contract.strike)] = snapshot
        conflict = getattr(ib, "market_data_session_conflict", None)
        if conflict:
            raise MarketDataSessionConflict(conflict)
    return quotes


def yahoo_put_quotes(puts: pd.DataFrame) -> dict[float, object]:
    """Adapt yfinance option rows to the small quote interface used below."""
    quotes: dict[float, object] = {}
    for _, put in puts.iterrows():
        strike = number(put.get("strike"))
        if strike is None:
            continue
        quotes[strike] = SimpleNamespace(
            bid=put.get("bid"),
            ask=put.get("ask"),
            putOpenInterest=put.get("openInterest"),
            volume=put.get("volume"),
            modelGreeks=SimpleNamespace(
                impliedVol=put.get("impliedVolatility"), delta=None
            ),
            contract=SimpleNamespace(localSymbol=put.get("contractSymbol", "")),
            quoteSource="Yahoo Finance fallback",
        )
    return quotes


def process_symbol(
    symbol: str,
    ib: IB,
    *,
    ticker: yf.Ticker | None = None,
    history: pd.DataFrame | None = None,
    info: dict | None = None,
    expirations: list[tuple[str, int]] | None = None,
    option_chains: dict[str, pd.DataFrame] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Process one symbol, reusing market data supplied by combined scanners."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    try:
        ticker = ticker or yf.Ticker(symbol)
        if history is None:
            history = ticker.history(period="1y", auto_adjust=True)
        if len(history) < 220:
            return accepted, [{"symbol": symbol, "reason": "insufficient price history"}]

        history = indicators(history)
        row, previous = history.iloc[-1], history.iloc[-2]
        price = number(row.get("Close"))
        avg_volume = number(row.get("AVG_VOL20"), 0)
        if price is None or price <= 0:
            return accepted, [{"symbol": symbol, "reason": "invalid stock price"}]
        if price > MAX_STOCK_PRICE:
            return accepted, [{"symbol": symbol, "reason": "stock exceeds collateral price limit"}]
        if avg_volume < MIN_UNDERLYING_AVG_VOLUME:
            return accepted, [{"symbol": symbol, "reason": "low underlying volume"}]

        if info is None:
            try:
                info = ticker.info or {}
            except Exception:
                info = {}
        quote_type = str(info.get("quoteType", "")).upper()
        is_fund = quote_type in {"ETF", "MUTUALFUND", "INDEX"}
        earnings = None if is_fund else next_earnings_date(ticker)
        earnings_days = (earnings - datetime.now().date()).days if earnings else None
        if not is_fund and earnings_days is None and REJECT_UNKNOWN_EARNINGS:
            return accepted, [{"symbol": symbol, "reason": "earnings date unknown"}]

        quality, quality_reasons = quality_score(row, previous, info)
        if earnings_days is None and not is_fund:
            quality = max(0, quality - 10)
            quality_reasons.append("WARNING: earnings date unknown")

        dividend_yield = number(info.get("dividendYield"), 0.0)
        # Some feeds express 4% as 4 instead of 0.04.
        if dividend_yield > 1:
            dividend_yield /= 100

        if expirations is None:
            expirations = valid_expirations(ticker)
        option_chains = option_chains if option_chains is not None else {}
        for expiry, dte in expirations:
            if earnings_days is not None and -EARNINGS_BUFFER_DAYS <= earnings_days <= dte + EARNINGS_BUFFER_DAYS:
                rejected.append({"symbol": symbol, "expiry": expiry, "reason": "earnings near/before expiration"})
                continue
            puts = option_chains.get(expiry)
            if puts is None:
                puts = ticker.option_chain(expiry).puts
                option_chains[expiry] = puts
            strikes = sorted({
                float(value)
                for value in puts.get("strike", pd.Series(dtype=float)).dropna()
                if value < price and MIN_OTM_PCT <= (price - value) / price * 100 <= 30
            })
            if not strikes:
                rejected.append({"symbol": symbol, "expiry": expiry, "reason": "no eligible strikes"})
                continue
            if getattr(ib, "market_data_disabled", False):
                ibkr_quotes = yahoo_put_quotes(puts)
            else:
                try:
                    ibkr_quotes = ibkr_put_quotes(ib, symbol, expiry, strikes)
                except MarketDataSessionConflict as exc:
                    ib.market_data_disabled = True
                    print(f"\nIBKR market-data session conflict: {exc}")
                    print(
                        "Continuing with Yahoo Finance option quotes. Results will be\n"
                        "labeled as fallback data; verify prices in IBKR before trading."
                    )
                    ibkr_quotes = yahoo_put_quotes(puts)
            for _, put in puts.iterrows():
                strike = number(put.get("strike"))
                quote = ibkr_quotes.get(strike) if strike is not None else None
                bid = number(getattr(quote, "bid", None))
                ask = number(getattr(quote, "ask", None))
                oi = number(getattr(quote, "putOpenInterest", None), 0)
                option_volume = number(getattr(quote, "volume", None), 0)
                model_greeks = getattr(quote, "modelGreeks", None)
                iv = number(getattr(model_greeks, "impliedVol", None))
                ibkr_delta = number(getattr(model_greeks, "delta", None))
                contract = getattr(getattr(quote, "contract", None), "localSymbol", "")
                quote_source = getattr(quote, "quoteSource", "IBKR")

                def reject(reason: str):
                    rejected.append({"symbol": symbol, "expiry": expiry, "contract": contract, "reason": reason})

                if quote is None or None in (strike, bid, ask) or min(strike, bid, ask) <= 0:
                    reject("missing/invalid IBKR bid or ask")
                    continue
                if strike >= price:
                    reject("not OTM")
                    continue
                otm_pct = (price - strike) / price * 100
                if otm_pct < MIN_OTM_PCT:
                    reject("too close to the money")
                    continue
                midpoint = (bid + ask) / 2
                spread_pct = (ask - bid) / midpoint * 100
                if spread_pct > MAX_SPREAD_PCT:
                    reject("bid/ask spread too wide")
                    continue
                # Some IBKR subscriptions omit open interest from snapshots. In
                # that case, current option volume may independently establish
                # liquidity; spread validation remains mandatory.
                if oi < MIN_OPEN_INTEREST and option_volume < MIN_OPTION_VOLUME:
                    reject("insufficient option liquidity")
                    continue

                estimated_delta, probability_itm = put_risk_estimates(
                    price, strike, dte, iv, dividend_yield
                ) if iv is not None else (None, None)
                abs_delta = abs(ibkr_delta) if ibkr_delta is not None else estimated_delta
                if abs_delta is None or probability_itm is None:
                    reject("could not estimate assignment risk")
                    continue
                if not MIN_ABS_DELTA <= abs_delta <= MAX_ABS_DELTA:
                    reject("delta outside risk budget")
                    continue
                if probability_itm > MAX_PROBABILITY_ITM:
                    reject("estimated ITM probability too high")
                    continue

                # Bid is used as a conservative executable-credit estimate.
                breakeven = strike - bid
                cash_secured = breakeven * 100
                return_pct = bid / breakeven * 100
                annualized_return_pct = return_pct * 365 / dte
                if annualized_return_pct < MIN_ANNUALIZED_RETURN_PCT:
                    reject("annualized premium return too low")
                    continue

                safety_score = clamp((MAX_ABS_DELTA - abs_delta) / (MAX_ABS_DELTA - MIN_ABS_DELTA) * 70, 0, 70)
                safety_score += clamp((otm_pct - MIN_OTM_PCT) * 3, 0, 30)
                income_score = clamp((annualized_return_pct - MIN_ANNUALIZED_RETURN_PCT) / 16 * 100, 0, 100)
                liquidity_score = clamp((oi - MIN_OPEN_INTEREST) / 9, 0, 60)
                liquidity_score += clamp((MAX_SPREAD_PCT - spread_pct) / MAX_SPREAD_PCT * 40, 0, 40)
                final_score = 0.35 * quality + 0.35 * safety_score + 0.20 * income_score + 0.10 * liquidity_score

                accepted.append({
                    "symbol": symbol,
                    "contract": contract,
                    "price": rounded(price),
                    "expiry": expiry,
                    "dte": dte,
                    "strike": rounded(strike),
                    "bid_credit": rounded(bid),
                    "cash_secured": rounded(cash_secured),
                    "breakeven": rounded(breakeven),
                    "otm_pct": rounded(otm_pct),
                    "abs_delta_est": rounded(abs_delta, 3),
                    "prob_itm_est_pct": rounded(probability_itm * 100, 1),
                    "iv_pct": rounded(iv * 100, 1) if iv is not None else None,
                    "return_on_cash_pct": rounded(return_pct),
                    "annualized_return_pct": rounded(annualized_return_pct),
                    "open_interest": int(oi),
                    "option_volume": int(option_volume),
                    "spread_pct": rounded(spread_pct),
                    "quality_score": rounded(quality),
                    "safety_score": rounded(safety_score),
                    "income_score": rounded(income_score),
                    "liquidity_score": rounded(liquidity_score),
                    "score": rounded(final_score),
                    "earnings_date": earnings.isoformat() if earnings else ("N/A (fund)" if is_fund else "UNKNOWN"),
                    "reasons": "; ".join(quality_reasons),
                    "risk_note": "Delta/probability are European-model estimates; assignment is never guaranteed against.",
                    "quote_source": quote_source,
                })
        return accepted, rejected
    except MarketDataSessionConflict:
        raise
    except Exception as exc:
        traceback.print_exc()
        return accepted, rejected + [{"symbol": symbol, "reason": f"symbol error: {exc}"}]


def main() -> None:
    print("=" * 88)
    print("CSP SCREENER V3 - premium optimized within an assignment-risk budget")
    print("=" * 88)
    print(f"Contract rules: {MIN_DTE}-{MAX_DTE} DTE, {MIN_ABS_DELTA:.2f}-{MAX_ABS_DELTA:.2f} abs delta, "
          f"max {MAX_PROBABILITY_ITM:.0%} estimated probability ITM")

    ib = IB()
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=10)
        install_ibkr_error_tracking(ib)
        ib.reqMarketDataType(IBKR_MARKET_DATA_TYPE)
        print(f"Connected to IBKR at {IBKR_HOST}:{IBKR_PORT} (client {IBKR_CLIENT_ID})")
    except Exception as exc:
        print(f"\nIBKR connection failed: {exc}")
        print("Start TWS or IB Gateway, log in, and enable API socket clients.")
        return

    candidates: list[dict] = []
    rejection_log: list[dict] = []
    try:
        for symbol in WATCHLIST:
            found, rejected = process_symbol(symbol, ib)
            candidates.extend(found)
            rejection_log.extend(rejected)
            print(f"{symbol:<6} {len(found):>3} qualifying contracts")
    except MarketDataSessionConflict as exc:
        print(f"\nIBKR market-data session conflict: {exc}")
        print(
            "Error 10197 means this IBKR username is consuming market data in another\n"
            "live TWS/IB Gateway/Client Portal session. Log out that other session, or\n"
            "connect this screener with a separate IBKR username that has market-data\n"
            "permissions. Changing clientId does not resolve an account-session conflict."
        )
        return
    finally:
        ib.disconnect()

    if not candidates:
        counts = Counter(item.get("reason", "unknown") for item in rejection_log)
        print("\nNo contracts met every risk and liquidity constraint. No trade is a valid result.")
        print("\nMost common rejection reasons:")
        for reason, count in counts.most_common(10):
            print(f"  {count:>5}  {reason}")
        unusable_quotes = counts.get("missing/invalid IBKR bid or ask", 0)
        if unusable_quotes and unusable_quotes >= len(rejection_log) * 0.5:
            print(
                "\nDATA WARNING: Most IBKR option rows have no usable bid/ask. Check that\n"
                "the account has US options market-data permissions, then re-run during\n"
                "regular market hours. Last trade is intentionally not substituted for bid."
            )
        return

    frame = pd.DataFrame(candidates).sort_values("score", ascending=False)
    # Retain the best risk-adjusted contract for each underlying.
    frame = frame.drop_duplicates("symbol", keep="first").sort_values("score", ascending=False)
    frame.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")

    columns = [
        "symbol", "score", "price", "strike", "bid_credit", "dte",
        "abs_delta_est", "prob_itm_est_pct", "otm_pct",
        "annualized_return_pct", "spread_pct", "earnings_date",
    ]
    print("\nTOP RISK-ADJUSTED CSP CANDIDATES")
    print(frame[columns].head(20).to_string(index=False))
    print(f"\nSaved {len(frame)} candidates to {OUTPUT_FILE}")
    print(f"Evaluated rejections: {len(rejection_log)}")
    print("Reminder: an open short equity put can still be assigned before expiration.")


if __name__ == "__main__":
    main()
