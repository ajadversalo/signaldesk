from pathlib import Path
from datetime import datetime

STATUS_DIR = (
    Path(__file__).resolve().parent
    / "logs"
    / "status"
)

STATUS_DIR.mkdir(
    parents=True,
    exist_ok=True
)


def get_today_status_file():

    return (
        STATUS_DIR
        / f"{datetime.now().date()}.txt"
    )


def save_status_report(text):

    status_file = (
        get_today_status_file()
    )

    with open(
        status_file,
        "a",
        encoding="utf-8"
    ) as f:

        f.write(
            "\n\n"
            + "=" * 50
            + "\n"
        )
        f.write("\n")
        f.write(
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
        f.write("\n")
        f.write("=" * 50)
        f.write("\n\n")

        f.write(text)

        f.write("\n\n")