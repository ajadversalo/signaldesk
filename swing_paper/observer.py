from ibkr_client import IBKRClient
from time import sleep

from config import WATCHLIST

from strategy import (
    evaluate_symbol,
    evaluate_exit
)

from position_state import (
    load_state
)

from observer_logger import (
    log_observation
)


def main():

    client = IBKRClient()

    client.connect(
        client_id=3
    )

    positions = client.get_positions()

    state = load_state()

    position_lookup = {

        p["symbol"]: p

        for p in positions

    }

    symbols = set(WATCHLIST)

    for p in positions:

        symbols.add(
            p["symbol"]
        )

    print()
    print("=" * 50)
    print("OBSERVER SNAPSHOT")
    print("=" * 50)

    for symbol in sorted(symbols):

        try:

            bars = client.get_daily_bars(
                symbol
            )

            result = evaluate_symbol(
                bars
            )

            exit_result = evaluate_exit(
                bars,
                state.get(symbol)
            )

            price = (
                bars["close"].iloc[-1]
            )

            owned = (
                symbol
                in position_lookup
            )

            entry_price = 0

            unrealized_pct = 0

            if owned:

                position = (
                    position_lookup[symbol]
                )

                entry_price = (
                    position["avg_cost"]
                )

                unrealized_pct = (

                    (price - entry_price)

                    / entry_price

                ) * 100

            log_observation(

                category=(
                    "owned"
                    if owned
                    else "watchlist"
                ),

                symbol=symbol,

                owned=owned,

                price=price,

                entry_price=entry_price,

                unrealized_pct=unrealized_pct,

                momentum=result["momentum"],

                acceleration=result["acceleration"],

                exit_signal=exit_result["exit_signal"],

                exit_reason=exit_result["reason"]

            )

            print(
                f"{symbol:<6} "
                f"Owned={owned}"
            )

        except Exception as e:

            print(
                f"{symbol:<6} "
                f"FAILED: {e}"
            )

    client.disconnect()


if __name__ == "__main__":

    while True:

        main()

        sleep(300)