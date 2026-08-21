from pathlib import Path
from datetime import datetime

import csv
import pandas as pd

EXIT_SIGNAL_DIR = (
    Path(__file__).resolve().parent
    / "logs"
    / "exit_signals"
)

EXIT_SIGNAL_DIR.mkdir(
    parents=True,
    exist_ok=True
)


def get_today_exit_file():

    return (
        EXIT_SIGNAL_DIR
        / f"{datetime.now().date()}.csv"
    )


def clear_today_exit_signals():

    signal_file = (
        get_today_exit_file()
    )

    if signal_file.exists():

        signal_file.unlink()


def save_pending_exit_signal(
    symbol,
    qty,
    reason
):

    signal_file = (
        get_today_exit_file()
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
                "qty",
                "reason"
            ])

        writer.writerow([
            datetime.now().date(),
            symbol,
            qty,
            reason
        ])


def load_latest_exit_batch():

    signal_files = sorted(
        EXIT_SIGNAL_DIR.glob("*.csv")
    )

    if not signal_files:

        return pd.DataFrame()

    latest_file = signal_files[-1]

    return pd.read_csv(
        latest_file
    )