from datetime import datetime
import math
import traceback
import numpy as np
import pandas as pd
import yfinance as yf

# ============================================================
# WATCHLIST
# ============================================================

WATCHLIST = [
    # Telecom / Utilities
    "TMUS",
    "T", "VZ",
    "WEC", "AEE", "DUK", "SO", "EXC",

    # Financials
    "JPM", "BAC", "WFC", "PNC", "USB",
    "GS", "MS", "AXP",
    "SCHW", "COF",
    "RY", "TD", "BNS", "CM",
    "SOFI",
    "XLF",

    # Healthcare
    "ABBV", "JNJ", "MRK", "GILD",
    "PFE", "BMY",
    "XLV",

    # Consumer Staples
    "KO", "PEP", "PG",
    "COST", "WMT",
    "KR", "GIS", "HSY", "KHC", "CPB",
    "XLP",

    # Retail / Consumer
    "HD", "LOW", "BBY", "TGT",

    # Technology
    "AAPL", "MSFT", "GOOGL", "AMZN",
    "ORCL", "CSCO", "IBM", "QCOM",
    "NFLX", "AMD", "SHOP",

    # Industrials
    "CAT", "GE", "HON",
    "UPS", "FDX",
    "XLI",

    # Energy
    "XOM", "CVX", "COP",
    "EOG", "FANG",
    "ENB", "SU", "CNQ",

    # REITs
    "O", "VICI", "SPG", "NNN", "WPC",

    # ETFs
    "SPY", "QQQ",
]

# ============================================================
# CONFIG
# ============================================================

OUTPUT_FILE = "csp_candidates_v2.csv"

MIN_DTE = 14
MAX_DTE = 21

MIN_OTM = 5.0
MAX_PRICE = 100

MIN_OPEN_INTEREST = 100
MIN_AVG_VOLUME = 1_000_000

EARNINGS_BLACKOUT_DAYS = 21

QUALITY_WEIGHT = 0.70
OPTION_WEIGHT = 0.30

# ============================================================
# CACHE SPY
# ============================================================

print("Downloading SPY...")

SPY_HISTORY = yf.download(
    "SPY",
    period="1y",
    progress=False,
    auto_adjust=True,
)

SPY_CLOSE = SPY_HISTORY["Close"]

if isinstance(SPY_CLOSE, pd.DataFrame):
    SPY_CLOSE = SPY_CLOSE.iloc[:, 0]

SPY_CLOSE = SPY_CLOSE.squeeze()

print(type(SPY_CLOSE))
print(SPY_CLOSE.head())

# ============================================================
# HELPERS
# ============================================================

def safe_round(value, digits=2):

    try:
        if pd.isna(value):
            return None
        return round(float(value), digits)
    except:
        return None


def clamp(value, low, high):

    return max(low, min(high, value))


# ============================================================
# EARNINGS
# ============================================================

def get_earnings_days(symbol):

    try:

        ticker = yf.Ticker(symbol)

        calendar = ticker.calendar

        if calendar is None:
            return None

        if len(calendar) == 0:
            return None

        earnings_date = calendar.index[0]

        if pd.isna(earnings_date):
            return None

        return (
            earnings_date.date()
            - datetime.now().date()
        ).days

    except:
        return None


# ============================================================
# HISTORY
# ============================================================

def get_history(symbol):

    ticker = yf.Ticker(symbol)

    hist = ticker.history(
        period="1y",
        auto_adjust=True
    )

    if len(hist) < 220:
        return None, None

    return ticker, hist


# ============================================================
# INDICATORS
# ============================================================

def calculate_indicators(df):
    print(type(df["Close"]))
    close = df["Close"]

    df["SMA20"] = close.rolling(20).mean()

    df["SMA50"] = close.rolling(50).mean()

    df["SMA200"] = close.rolling(200).mean()

    # EMA

    ema12 = close.ewm(span=12).mean()

    ema26 = close.ewm(span=26).mean()

    df["MACD"] = ema12 - ema26

    df["MACD_SIGNAL"] = (
        df["MACD"]
        .ewm(span=9)
        .mean()
    )

    # RSI

    delta = close.diff()

    gain = delta.clip(lower=0)

    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()

    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss

    df["RSI"] = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    # ROC

    df["ROC20"] = (
        close
        .pct_change(20)
        * 100
    )

    # Average Volume

    df["AVG_VOL20"] = (
        df["Volume"]
        .rolling(20)
        .mean()
    )

    return df


# ============================================================
# RELATIVE STRENGTH
# ============================================================

def relative_strength(stock_close):

    stock1 = (
        stock_close.iloc[-1]
        /
        stock_close.iloc[-21]
        - 1
    )

    spy1 = (
        SPY_CLOSE.iloc[-1]
        /
        SPY_CLOSE.iloc[-21]
        - 1
    )

    stock3 = (
        stock_close.iloc[-1]
        /
        stock_close.iloc[-63]
        - 1
    )

    spy3 = (
        SPY_CLOSE.iloc[-1]
        /
        SPY_CLOSE.iloc[-63]
        - 1
    )

    stock6 = (
        stock_close.iloc[-1]
        /
        stock_close.iloc[-126]
        - 1
    )

    spy6 = (
        SPY_CLOSE.iloc[-1]
        /
        SPY_CLOSE.iloc[-126]
        - 1
    )

    return (

        (stock1 - spy1) * 100,

        (stock3 - spy3) * 100,

        (stock6 - spy6) * 100,

    )


# ============================================================
# EXPIRATIONS
# ============================================================

def get_valid_expirations(ticker):

    valid = []

    today = datetime.now().date()

    for expiry in ticker.options:

        dte = (
            datetime.strptime(
                expiry,
                "%Y-%m-%d"
            ).date()
            - today
        ).days

        if MIN_DTE <= dte <= MAX_DTE:

            valid.append(
                (
                    expiry,
                    dte
                )
            )

    return valid

# ============================================================
# QUALITY SCORING
# ============================================================

def score_trend(row):

    score = 0
    reasons = []

    price = row["Close"]

    sma20 = row["SMA20"]
    sma50 = row["SMA50"]
    sma200 = row["SMA200"]

    if price > sma20:
        score += 5
        reasons.append("Above SMA20")

    if price > sma50:
        score += 5
        reasons.append("Above SMA50")

    if price > sma200:
        score += 10
        reasons.append("Above SMA200")

    if sma20 > sma50:
        score += 5
        reasons.append("SMA20>SMA50")

    if sma50 > sma200:
        score += 5
        reasons.append("SMA50>SMA200")

    if row["SMA200"] > row_prev["SMA200"]:
        score += 5
        reasons.append("Rising SMA200")

    return score, reasons


# ============================================================
# MOMENTUM
# ============================================================

def score_momentum(row):

    score = 0
    reasons = []

    rsi = row["RSI"]

    if 50 <= rsi <= 70:
        score += 5
        reasons.append(f"RSI {rsi:.1f}")

    if row["MACD"] > row["MACD_SIGNAL"]:
        score += 5
        reasons.append("MACD Bullish")

    if row["ROC20"] > 0:
        score += 5
        reasons.append("Positive ROC20")

    if row["ROC20"] > 5:
        score += 5
        reasons.append("Strong Momentum")

    return score, reasons


# ============================================================
# RELATIVE STRENGTH
# ============================================================

def score_relative_strength(rs1, rs3, rs6):

    score = 0
    reasons = []

    if rs1 > 0:
        score += 5
        reasons.append("Beats SPY 1M")

    if rs3 > 0:
        score += 5
        reasons.append("Beats SPY 3M")

    if rs6 > 0:
        score += 5
        reasons.append("Beats SPY 6M")

    return score, reasons


# ============================================================
# FUNDAMENTALS
# ============================================================

def score_fundamentals(info):

    score = 0
    reasons = []

    revenue_growth = info.get("revenueGrowth")
    earnings_growth = info.get("earningsGrowth")
    market_cap = info.get("marketCap")
    trailing_pe = info.get("trailingPE")

    if revenue_growth is not None and revenue_growth > 0:
        score += 3
        reasons.append("Revenue Growing")

    if earnings_growth is not None and earnings_growth > 0:
        score += 3
        reasons.append("EPS Growing")

    if trailing_pe is not None and trailing_pe > 0:
        score += 4
        reasons.append("Profitable")

    return score, reasons


# ============================================================
# RISK
# ============================================================

def score_risk(info, earnings_days):

    score = 0
    reasons = []

    market_cap = info.get("marketCap", 0)
    beta = info.get("beta")
    debt = info.get("debtToEquity")
    fcf = info.get("freeCashflow")

    if market_cap >= 20_000_000_000:
        score += 2
        reasons.append("Large Cap")

    if beta is not None and beta < 2:
        score += 2
        reasons.append("Reasonable Beta")

    if debt is not None and debt < 150:
        score += 2
        reasons.append("Debt OK")

    if fcf is not None and fcf > 0:
        score += 2
        reasons.append("Positive FCF")

    if (
        earnings_days is None
        or
        earnings_days > EARNINGS_BLACKOUT_DAYS
    ):
        score += 2
        reasons.append("No Earnings")

    return score, reasons


# ============================================================
# LIQUIDITY
# ============================================================

def score_liquidity(avg_volume, open_interest, spread_pct):

    score = 0
    reasons = []

    if avg_volume >= MIN_AVG_VOLUME:
        score += 4
        reasons.append("High Volume")

    if open_interest >= 500:
        score += 4
        reasons.append("High OI")

    if spread_pct <= 5:
        score += 2
        reasons.append("Tight Spread")

    return score, reasons


# ============================================================
# OPTION SCORE
# ============================================================

def score_option(
    yield_pct,
    otm_pct,
    open_interest,
    dte,
):

    score = 0

    score += clamp(yield_pct * 12, 0, 35)

    score += clamp(otm_pct * 3, 0, 25)

    score += clamp(open_interest / 50, 0, 20)

    if 14 <= dte <= 21:
        score += 20
    elif dte <= 30:
        score += 10

    return min(score, 100)


# ============================================================
# STARS
# ============================================================

def stars(score):

    if score >= 95:
        return "★★★★★"

    if score >= 90:
        return "★★★★☆"

    if score >= 85:
        return "★★★★"

    if score >= 80:
        return "★★★☆"

    if score >= 70:
        return "★★☆"

    return "REJECT"

# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    try:

        ticker, hist = get_history(symbol)

        if hist is None:
            print(f"{symbol:<6} Not enough history")
            return []

        hist = calculate_indicators(hist)

        row = hist.iloc[-1]

        print(type(row["Close"]))
        print(row["Close"])

        row_prev = hist.iloc[-2]

        price = float(row["Close"])

        if price > MAX_PRICE:
            print(f"{symbol:<6} Skip (${price:.2f})")
            return []

        earnings_days = get_earnings_days(symbol)

        if (
            earnings_days is not None
            and
            earnings_days <= EARNINGS_BLACKOUT_DAYS
        ):
            print(
                f"{symbol:<6} Earnings in {earnings_days} days"
            )
            return []

        rs1, rs3, rs6 = relative_strength(
            hist["Close"]
        )

        # --------------------------------------------------
        # QUALITY
        # --------------------------------------------------

        trend_score = 0
        trend_reasons = []

        if price > row["SMA20"]:
            trend_score += 5
            trend_reasons.append("Above SMA20")

        if price > row["SMA50"]:
            trend_score += 5
            trend_reasons.append("Above SMA50")

        if price > row["SMA200"]:
            trend_score += 10
            trend_reasons.append("Above SMA200")

        if row["SMA20"] > row["SMA50"]:
            trend_score += 5
            trend_reasons.append("SMA20>SMA50")

        if row["SMA50"] > row["SMA200"]:
            trend_score += 5
            trend_reasons.append("SMA50>SMA200")

        if row["SMA200"] > row_prev["SMA200"]:
            trend_score += 5
            trend_reasons.append("Rising SMA200")

        momentum_score, momentum_reasons = score_momentum(row)

        rs_score, rs_reasons = score_relative_strength(
            rs1,
            rs3,
            rs6
        )

        info = ticker.info

        fundamentals_score, fundamentals_reasons = (
            score_fundamentals(info)
        )

        risk_score, risk_reasons = score_risk(
            info,
            earnings_days
        )

        quality_reasons = (
            trend_reasons
            + momentum_reasons
            + rs_reasons
            + fundamentals_reasons
            + risk_reasons
        )

        quality_score = (
            trend_score
            + momentum_score
            + rs_score
            + fundamentals_score
            + risk_score
        )

        expirations = get_valid_expirations(ticker)

        candidates = []

        for expiry, dte in expirations:

            chain = ticker.option_chain(expiry)

            puts = chain.puts

            for _, put in puts.iterrows():

                strike = float(put["strike"])

                if strike >= price:
                    continue

                otm_pct = (
                    (price - strike)
                    / price
                ) * 100

                if otm_pct < MIN_OTM:
                    continue

                bid = float(
                    put.get("bid", 0)
                )

                ask = float(
                    put.get("ask", 0)
                )

                if bid <= 0 or ask <= 0:
                    continue

                premium = (
                    bid + ask
                ) / 2

                oi = int(
                    put.get(
                        "openInterest",
                        0
                    )
                )

                if oi < MIN_OPEN_INTEREST:
                    continue

                spread_pct = (
                    (ask - bid)
                    / ask
                ) * 100

                liquidity_score, liquidity_reasons = (
                    score_liquidity(
                        row["AVG_VOL20"],
                        oi,
                        spread_pct
                    )
                )

                total_quality = (
                    quality_score
                    + liquidity_score
                )

                yield_pct = (
                    premium
                    / strike
                ) * 100

                option_score = score_option(
                    yield_pct,
                    otm_pct,
                    oi,
                    dte
                )

                final_score = (
                    total_quality
                    * QUALITY_WEIGHT
                    +
                    option_score
                    * OPTION_WEIGHT
                )

                candidates.append({

                    "symbol": symbol,

                    "price": safe_round(price),

                    "expiry": expiry,

                    "dte": dte,

                    "strike": strike,

                    "premium": safe_round(premium),

                    "yield_pct": safe_round(yield_pct),

                    "otm_pct": safe_round(otm_pct),

                    "quality": safe_round(total_quality),

                    "option": safe_round(option_score),

                    "score": safe_round(final_score),

                    "rating": stars(final_score),

                    "trend": trend_score,

                    "momentum": momentum_score,

                    "strength": rs_score,

                    "fundamentals": fundamentals_score,

                    "risk": risk_score,

                    "liquidity": liquidity_score,

                    "reasons": "; ".join(
                        quality_reasons
                        +
                        liquidity_reasons
                    ),

                })

        print(
            f"{symbol:<6} {len(candidates)} candidates"
        )

        return candidates

    except Exception:

        print(f"\n===== {symbol} =====")
        traceback.print_exc()

        return []
    
# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    try:

        ticker, hist = get_history(symbol)

        if hist is None:
            print(f"{symbol:<6} Not enough history")
            return []

        hist = calculate_indicators(hist)

        row = hist.iloc[-1]
        row_prev = hist.iloc[-2]

        price = float(row["Close"])

        if price > MAX_PRICE:
            print(f"{symbol:<6} Skip (${price:.2f})")
            return []

        earnings_days = get_earnings_days(symbol)

        if (
            earnings_days is not None
            and
            earnings_days <= EARNINGS_BLACKOUT_DAYS
        ):
            print(
                f"{symbol:<6} Earnings in {earnings_days} days"
            )
            return []

        rs1, rs3, rs6 = relative_strength(
            hist["Close"]
        )

        # --------------------------------------------------
        # QUALITY
        # --------------------------------------------------

        trend_score = 0
        trend_reasons = []

        if price > row["SMA20"]:
            trend_score += 5
            trend_reasons.append("Above SMA20")

        if price > row["SMA50"]:
            trend_score += 5
            trend_reasons.append("Above SMA50")

        if price > row["SMA200"]:
            trend_score += 10
            trend_reasons.append("Above SMA200")

        if row["SMA20"] > row["SMA50"]:
            trend_score += 5
            trend_reasons.append("SMA20>SMA50")

        if row["SMA50"] > row["SMA200"]:
            trend_score += 5
            trend_reasons.append("SMA50>SMA200")

        if row["SMA200"] > row_prev["SMA200"]:
            trend_score += 5
            trend_reasons.append("Rising SMA200")

        momentum_score, momentum_reasons = score_momentum(row)

        rs_score, rs_reasons = score_relative_strength(
            rs1,
            rs3,
            rs6
        )

        info = ticker.info

        fundamentals_score, fundamentals_reasons = (
            score_fundamentals(info)
        )

        risk_score, risk_reasons = score_risk(
            info,
            earnings_days
        )

        quality_reasons = (
            trend_reasons
            + momentum_reasons
            + rs_reasons
            + fundamentals_reasons
            + risk_reasons
        )

        quality_score = (
            trend_score
            + momentum_score
            + rs_score
            + fundamentals_score
            + risk_score
        )

        expirations = get_valid_expirations(ticker)

        candidates = []

        for expiry, dte in expirations:

            chain = ticker.option_chain(expiry)

            puts = chain.puts

            for _, put in puts.iterrows():

                strike = float(put["strike"])

                if strike >= price:
                    continue

                otm_pct = (
                    (price - strike)
                    / price
                ) * 100

                if otm_pct < MIN_OTM:
                    continue

                bid = float(
                    put.get("bid", 0)
                )

                ask = float(
                    put.get("ask", 0)
                )

                if bid <= 0 or ask <= 0:
                    continue

                premium = (
                    bid + ask
                ) / 2

                oi = int(
                    put.get(
                        "openInterest",
                        0
                    )
                )

                if oi < MIN_OPEN_INTEREST:
                    continue

                spread_pct = (
                    (ask - bid)
                    / ask
                ) * 100

                liquidity_score, liquidity_reasons = (
                    score_liquidity(
                        row["AVG_VOL20"],
                        oi,
                        spread_pct
                    )
                )

                total_quality = (
                    quality_score
                    + liquidity_score
                )

                yield_pct = (
                    premium
                    / strike
                ) * 100

                option_score = score_option(
                    yield_pct,
                    otm_pct,
                    oi,
                    dte
                )

                final_score = (
                    total_quality
                    * QUALITY_WEIGHT
                    +
                    option_score
                    * OPTION_WEIGHT
                )

                candidates.append({

                    "symbol": symbol,

                    "price": safe_round(price),

                    "expiry": expiry,

                    "dte": dte,

                    "strike": strike,

                    "premium": safe_round(premium),

                    "yield_pct": safe_round(yield_pct),

                    "otm_pct": safe_round(otm_pct),

                    "quality": safe_round(total_quality),

                    "option": safe_round(option_score),

                    "score": safe_round(final_score),

                    "rating": stars(final_score),

                    "trend": trend_score,

                    "momentum": momentum_score,

                    "strength": rs_score,

                    "fundamentals": fundamentals_score,

                    "risk": risk_score,

                    "liquidity": liquidity_score,

                    "reasons": "; ".join(
                        quality_reasons
                        +
                        liquidity_reasons
                    ),

                })

        print(
            f"{symbol:<6} {len(candidates)} candidates"
        )

        return candidates

    except Exception:
        print(f"\n===== {symbol} =====")
        traceback.print_exc()
        return []
    
# ============================================================
# MAIN
# ============================================================

def print_candidate(candidate):

    print("=" * 80)

    print(
        f"{candidate['symbol']}   "
        f"{candidate['rating']}"
    )

    print()

    print(
        f"Final Score : {candidate['score']}"
    )

    print(
        f"Quality     : {candidate['quality']}"
    )

    print(
        f"Option      : {candidate['option']}"
    )

    print()

    print(
        f"Price        : ${candidate['price']}"
    )

    print(
        f"Strike       : ${candidate['strike']}"
    )

    print(
        f"Premium      : ${candidate['premium']}"
    )

    print(
        f"Yield        : {candidate['yield_pct']}%"
    )

    print(
        f"OTM          : {candidate['otm_pct']}%"
    )

    print(
        f"DTE          : {candidate['dte']}"
    )

    print()

    print("Component Scores")

    print(
        f"Trend         : {candidate['trend']:>2}/35"
    )

    print(
        f"Momentum      : {candidate['momentum']:>2}/20"
    )

    print(
        f"Strength      : {candidate['strength']:>2}/15"
    )

    print(
        f"Fundamentals  : {candidate['fundamentals']:>2}/10"
    )

    print(
        f"Risk          : {candidate['risk']:>2}/10"
    )

    print(
        f"Liquidity     : {candidate['liquidity']:>2}/10"
    )

    print()

    print("Reasons")

    for reason in candidate["reasons"].split(";"):

        reason = reason.strip()

        if reason:

            print(f"  ✓ {reason}")

    print("=" * 80)
    print()


def main():

    print()
    print("=" * 80)
    print("CSP SCREENER V2")
    print("=" * 80)
    print()

    all_candidates = []

    for symbol in WATCHLIST:

        results = process_symbol(symbol)

        all_candidates.extend(results)

    if not all_candidates:

        print("No candidates found.")
        return

    df = pd.DataFrame(all_candidates)

    #
    # Keep only best option per stock
    #

    df = (
        df
        .sort_values(
            "score",
            ascending=False
        )
        .drop_duplicates(
            subset=["symbol"],
            keep="first"
        )
    )

    #
    # Overall ranking
    #

    df = df.sort_values(
        "score",
        ascending=False
    )

    #
    # Save CSV
    #

    df.to_csv(
        OUTPUT_FILE,
        index=False
    )

    #
    # Console Summary
    #

    print()
    print("=" * 80)
    print("TOP CSP CANDIDATES")
    print("=" * 80)

    summary_cols = [

        "symbol",

        "rating",

        "score",

        "quality",

        "option",

        "price",

        "strike",

        "yield_pct",

        "dte",

    ]

    print()

    print(

        df[
            summary_cols
        ].head(20).to_string(index=False)

    )

    print()

    print("=" * 80)
    print("TOP PICKS")
    print("=" * 80)
    print()

    for _, row in df.head(10).iterrows():

        print_candidate(row)

    print()

    print("=" * 80)
    print(f"Saved to {OUTPUT_FILE}")
    print("=" * 80)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    main()