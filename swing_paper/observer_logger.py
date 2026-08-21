from pathlib import Path
from datetime import datetime
import csv

OWNED_DIR = (
    Path(__file__).resolve().parent
    / "logs"
    / "observer"
    / "owned"
)

WATCHLIST_DIR = (
    Path(__file__).resolve().parent
    / "logs"
    / "observer"
    / "watchlist"
)

OWNED_DIR.mkdir(
    parents=True,
    exist_ok=True
)

WATCHLIST_DIR.mkdir(
    parents=True,
    exist_ok=True
)


def log_observation(

    category,

    symbol,

    owned,

    price,

    entry_price,

    unrealized_pct,

    momentum,

    acceleration,

    exit_signal,

    exit_reason

):

    filename = (
        datetime.now().strftime("%Y-%m-%d")
        + ".csv"
    )

    if category == "owned":

        log_file = (
            OWNED_DIR
            / filename
        )

    else:

        log_file = (
            WATCHLIST_DIR
            / filename
        )

    file_exists = (
        log_file.exists()
    )

    with open(
        log_file,
        "a",
        newline=""
    ) as f:

        writer = csv.writer(f)

        if not file_exists:

            writer.writerow([
                "timestamp",
                "symbol",
                "owned",
                "price",
                "entry_price",
                "unrealized_pct",
                "momentum",
                "acceleration",
                "exit_signal",
                "exit_reason"
            ])

        writer.writerow([
            datetime.now().isoformat(),
            symbol,
            owned,
            round(price, 2),
            round(entry_price, 2),
            round(unrealized_pct, 2),
            round(momentum, 2),
            round(acceleration, 2),
            exit_signal,
            exit_reason
        ])