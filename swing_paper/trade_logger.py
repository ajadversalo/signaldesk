from pathlib import Path
from datetime import datetime
import csv

LOG_DIR = (
    Path(__file__).resolve().parent
    / "logs"
    / "trades"
)

LOG_DIR.mkdir(
    parents=True,
    exist_ok=True
)


def log_trade(
    symbol,
    action,
    shares,
    price,
    score,
    momentum=0,
    acceleration=0,
    rvol=0,
    pnl_dollars=0,
    pnl_percent=0,
    exit_reason=""
):

    filename = (
        datetime.now()
        .strftime("%Y-%m")
        + ".csv"
    )

    log_file = (
        LOG_DIR
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
                "action",
                "shares",
                "price",
                "score",
                "momentum",
                "acceleration",
                "rvol",
                "pnl_dollars",
                "pnl_percent",
                "exit_reason"
            ])

        writer.writerow([
            datetime.now().isoformat(),
            symbol,
            action,
            shares,
            round(price, 2),
            round(score, 2),
            round(momentum, 2),
            round(acceleration, 2),
            round(rvol, 2),
            round(pnl_dollars, 2),
            round(pnl_percent, 2),
            exit_reason
        ])