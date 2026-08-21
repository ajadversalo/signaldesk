from pathlib import Path

import pandas as pd

from config import (
    EARNINGS_BLACKOUT_DAYS
)

EARNINGS_FILE = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "earnings"
    / "earnings_calendar.csv"
)

earnings_df = pd.read_csv(
    EARNINGS_FILE
)

earnings_df["EarningsDate"] = pd.to_datetime(
    earnings_df["EarningsDate"]
)

def get_days_to_earnings(symbol):

    today = pd.Timestamp.today().normalize()

    rows = earnings_df[
        earnings_df["Ticker"] == symbol
    ]

    future_rows = rows[
        rows["EarningsDate"] >= today
    ]

    if future_rows.empty:

        return None

    next_date = (
        future_rows["EarningsDate"]
        .min()
    )

    return (
        next_date - today
    ).days

def earnings_ok(symbol):

    days = get_days_to_earnings(
        symbol
    )

    if days is None:

        return True

    return (
        days > EARNINGS_BLACKOUT_DAYS
    )

