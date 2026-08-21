from pathlib import Path
import pandas as pd
import sys

# ============================================
# ARGUMENT
# ============================================

if len(sys.argv) < 2:

    print("")
    print(
        "Usage:"
    )

    print(
        "python forex_metrics.py EURUSD"
    )

    raise SystemExit

SYMBOL = sys.argv[1].upper()

# ============================================
# FILES
# ============================================

INPUT_FILE = (
    Path(__file__).resolve().parent
    / f"{SYMBOL}_5min.csv"
)

OUTPUT_FILE = (
    Path(__file__).resolve().parent
    / f"{SYMBOL}_5min_metrics.csv"
)

# ============================================
# LOAD
# ============================================

df = pd.read_csv(
    INPUT_FILE
)

df = df.rename(
    columns={
        "date": "Datetime",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close"
    }
)

df["Datetime"] = pd.to_datetime(
    df["Datetime"]
)

# ============================================
# PREVIOUS CLOSE
# ============================================

df["Prev_Close"] = (
    df["Close"]
    .shift(1)
)

# ============================================
# TRUE RANGE
# ============================================

df["TR1"] = (
    df["High"]
    - df["Low"]
)

df["TR2"] = (
    df["High"]
    - df["Prev_Close"]
).abs()

df["TR3"] = (
    df["Low"]
    - df["Prev_Close"]
).abs()

df["True_Range"] = df[
    ["TR1", "TR2", "TR3"]
].max(axis=1)

# ============================================
# ATR
# ============================================

df["ATR_14"] = (
    df["True_Range"]
    .rolling(14)
    .mean()
)

# ============================================
# BREAKOUT LEVELS
# ============================================

df["High_20"] = (
    df["High"]
    .rolling(20)
    .max()
    .shift(1)
)

df["Low_20"] = (
    df["Low"]
    .rolling(20)
    .min()
    .shift(1)
)

# ============================================
# BREAKOUTS
# ============================================

df["Breakout_20_Up"] = (
    df["Close"]
    > df["High_20"]
)

df["Breakout_20_Down"] = (
    df["Close"]
    < df["Low_20"]
)

# ============================================
# SAVE
# ============================================

df.to_csv(
    OUTPUT_FILE,
    index=False
)

print("")
print(
    f"Rows: {len(df):,}"
)

print(
    df["Datetime"].min()
)

print(
    df["Datetime"].max()
)

print("")
print(
    f"Saved:"
)

print(
    OUTPUT_FILE
)