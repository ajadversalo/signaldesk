from pathlib import Path
from datetime import datetime

import csv
import pandas as pd

SIGNAL_DIR = (
    Path(__file__).resolve().parent
    / "logs"
    / "signals"
)

SIGNAL_DIR.mkdir(
    parents=True,
    exist_ok=True
)


def get_today_signal_file():

    return (
        SIGNAL_DIR
        / f"{datetime.now().date()}.csv"
    )


def clear_today_signals():

    signal_file = (
        get_today_signal_file()
    )

    if signal_file.exists():

        signal_file.unlink()


def save_pending_signal(
    symbol,
    momentum,
    acceleration,
    rvol,
    score
):

    signal_file = (
        get_today_signal_file()
    )

    file_exists = (
        signal_file.exists()
    )

    with open(
        signal_file,
        "a",
        newline=""
    ) as f:

        writer = csv.writer(f)

        if not file_exists:

            writer.writerow([
                "date",
                "symbol",
                "momentum",
                "acceleration",
                "rvol",
                "score"
            ])

        writer.writerow([
            datetime.now().date(),
            symbol,
            round(momentum, 2),
            round(acceleration, 2),
            round(rvol, 2),
            round(score, 2)
        ])


def load_latest_signal_batch():

    signal_files = sorted(
        SIGNAL_DIR.glob("*.csv")
    )

    if not signal_files:

        return pd.DataFrame()

    latest_file = signal_files[-1]

    return pd.read_csv(
        latest_file
    )