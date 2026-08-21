# ibkr_paper/paper_trader.py

from ibkr_client import IBKRClient
from strategy import (
    evaluate_symbol,
    confirm_entry,
    evaluate_exit
)

from market_data import (
    get_current_price,
    get_today_open
)

from config import (
    WATCHLIST,
    POSITION_SIZE,
    ENABLE_PAPER_ORDERS,
    ENABLE_MORNING_CONFIRMATION,
    RUN_MODE,
    MAX_NEW_PURCHASES_PER_DAY
)
from trade_logger import log_trade
from earnings import (earnings_ok, get_days_to_earnings)
from signal_logger import (
    save_pending_signal,
    load_latest_signal_batch,
    clear_today_signals
)

from exit_signal_logger import (
    save_pending_exit_signal,
    load_latest_exit_batch,
    clear_today_exit_signals
)

import traceback
import sys
import pandas as pd

from status_logger import (
    save_status_report
)

from position_state import (
    load_state,
    save_state
)

RUN_MODE = "SCAN"

if len(sys.argv) > 1:

    RUN_MODE = (
        sys.argv[1]
        .upper()
    )

def main():

    client = IBKRClient()

    client.connect(
        client_id=2
    )

    cash = client.get_cash()

    equity = client.get_equity()

    positions = client.get_positions()

    # Remove forex/cash positions
    positions = [
        p for p in positions
        if p["symbol"] != "GBP"
    ]

    if RUN_MODE == "SCAN":

        clear_today_signals()

    if RUN_MODE == "BUY":

        pending_signals = (
            load_latest_signal_batch()
        )

        print()
        print("=" * 50)
        print("CONFIRMATION MODE")
        print("=" * 50)

        print(
            f"Loaded "
            f"{len(pending_signals)} "
            f"signals"
        )

        print()

        confirmed = []

        for _, signal in pending_signals.iterrows():

            symbol = signal["symbol"]

            bars = client.get_daily_bars(
                symbol
            )

            result = evaluate_symbol(
                bars
            )

            confirmation = confirm_entry(
                bars
            )

            # MORNING PRICE CHECK
            price_confirmed = True

            if ENABLE_MORNING_CONFIRMATION:

                current_price = (
                    get_current_price(
                        symbol
                    )
                )

                today_open = (
                    get_today_open(
                        symbol
                    )
                )

                price_confirmed = False

                if (
                    current_price is not None
                    and
                    today_open is not None
                ):

                    price_confirmed = (
                        current_price > today_open
                    )

            if (  
                    result["signal"]
                    and confirmation["confirmed"]
                    and price_confirmed
                ):

                    confirmed.append(
                        symbol
                    )

                    close = bars["close"].iloc[-1]

                    shares = int(
                        POSITION_SIZE
                        / close
                    )

                    if shares > 0:

                        print(
                            f"{symbol:<6} "
                            f"CONFIRMED "
                            f"BUY {shares}"
                        )

                        if ENABLE_PAPER_ORDERS:

                            trade = client.place_market_buy(
                                symbol,
                                shares
                            )

                            fill_price = trade.orderStatus.avgFillPrice

                            if fill_price == 0 and trade.fills:
                                fill_price = trade.fills[-1].execution.price

                            client.ib.sleep(2)

                            state = load_state()

                            log_trade(

                                symbol=symbol,

                                action="BUY",

                                shares=shares,

                                price=fill_price,

                                score=result["score"],

                                momentum=result["momentum"],

                                acceleration=result["acceleration"],

                                rvol=result["rvol"]

                            )

                            state[symbol] = {

                                "entry_date":
                                    pd.Timestamp.now()
                                    .strftime("%Y-%m-%d"),

                                "entry_price":
                                    fill_price,

                                "highest_close":
                                    fill_price,

                                "max_momentum":
                                    result["momentum"],

                                "max_favorable_pct":
                                    0
                            }

                            save_state(state)

                    else:

                        print(
                            f"{symbol:<6} "
                            f"CONFIRMED "
                            f"BUT POSITION TOO SMALL"
                        )

            else:

                print(
                    f"{symbol:<6} "
                    f"REJECTED "
                    f"Current={current_price:.2f} "
                    f"Open={today_open:.2f} "
                    f"P={price_confirmed}"
                )
        print()

        print(
            f"Confirmed: "
            f"{len(confirmed)}"
        )

        client.disconnect()

        return
    
    if RUN_MODE == "EXIT":

        print()
        print("=" * 50)
        print("EXIT MODE")
        print("=" * 50)

        clear_today_exit_signals()

        exit_orders = []

        for p in positions:

            symbol = p["symbol"]

            bars = client.get_daily_bars(
                symbol
            )

            state = load_state()

            exit_result = evaluate_exit(
                bars,
                state.get(symbol)
            )

            if exit_result["exit_signal"]:

                exit_orders.append({

                    "symbol": symbol,

                    "qty": p["qty"],

                    "reason":
                        exit_result["reason"]

                })

                save_pending_exit_signal(

                    symbol=symbol,

                    qty=p["qty"],

                    reason=exit_result["reason"]

                )

                print(
                    f"{symbol:<6} "
                    f"SELL "
                    f"{p['qty']} "
                    f"Reason="
                    f"{exit_result['reason']}"
                )

        print()

        print(
            f"Exit Orders: "
            f"{len(exit_orders)}"
        )

        # if ENABLE_PAPER_ORDERS:

        #     print()
        #     print("=" * 50)
        #     print("SELL SUBMISSION")
        #     print("=" * 50)

        #     for order in exit_orders:

        #         print(
        #             f"Sending SELL "
        #             f"{order['symbol']} "
        #             f"Qty={order['qty']}"
        #         )

        #         client.place_market_sell(

        #             order["symbol"],

        #             order["qty"]

        #         )

        #         state = load_state()

        #         state.pop(
        #             order["symbol"],
        #             None
        #         )

        #         save_state(state)

        #         log_trade(

        #             symbol=order["symbol"],

        #             action="SELL",

        #             shares=order["qty"],

        #             price=0,

        #             score=0

        #         )

        client.disconnect()

        return
    
    if RUN_MODE == "SELL":

        print()
        print("=" * 50)
        print("SELL MODE")
        print("=" * 50)

        pending_exits = (
            load_latest_exit_batch()
        )

        print(
            f"Loaded "
            f"{len(pending_exits)} "
            f"exit signals"
        )

        for _, order in pending_exits.iterrows():

            symbol = order["symbol"]

            position = next(
                (
                    p for p in positions
                    if p["symbol"] == symbol
                ),
                None
            )

            if not position:
                continue

            qty = position["qty"]

            print(
                f"Sending SELL "
                f"{symbol} "
                f"Qty={qty}"
            )

            if ENABLE_PAPER_ORDERS:

                trade = client.place_market_sell(
                    symbol,
                    qty
                )

                client.ib.sleep(2)

                exit_price = trade.orderStatus.avgFillPrice

                if exit_price == 0 and trade.fills:
                    exit_price = trade.fills[-1].execution.price

                state = load_state()

                entry_data = state.get(
                    symbol,
                    {}
                )

                entry_price = (
                    position["avg_cost"]
                )

                pnl_dollars = (
                    exit_price - entry_price
                ) * qty

                pnl_percent = (
                    (exit_price - entry_price)
                    / entry_price
                ) * 100


                log_trade(

                    symbol=symbol,

                    action="SELL",

                    shares=qty,

                    price=exit_price,

                    score=entry_data.get(
                        "entry_score",
                        0
                    ),

                    momentum=entry_data.get(
                        "entry_momentum",
                        0
                    ),

                    acceleration=entry_data.get(
                        "entry_acceleration",
                        0
                    ),

                    rvol=entry_data.get(
                        "entry_rvol",
                        0
                    ),

                    pnl_dollars=pnl_dollars,

                    pnl_percent=pnl_percent,

                    exit_reason=order["reason"]

                )
                        
        client.disconnect()

        return

    position_lookup = {

        p["symbol"]: p

        for p in positions

    }

    # ============================================================
    # STATUS MODE
    # ============================================================

    if RUN_MODE == "STATUS":

        from pathlib import Path
        from datetime import datetime

        status_dir = (
            Path(__file__).resolve().parent
            / "logs"
            / "status"
        )

        status_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        status_file = (
            status_dir
            / f"{datetime.now().date()}.txt"
        )

        report = []
        
        line = "=" * 50
        print(line)
        report.append(line)

        line = "ACCOUNT STATUS"
        print(line)
        report.append(line)

        line = "=" * 50
        print(line)
        report.append(line)

        total_market_value = 0
        total_unrealized_pnl = 0

        if not positions:

            line = "No positions"
            print(line)
            report.append(line)

        else:

            for p in positions:

                symbol = p["symbol"]
                qty = p["qty"]
                avg_cost = p["avg_cost"]

                bars = client.get_daily_bars(
                    symbol
                )

                current_price = (
                    bars["close"].iloc[-1]
                )

                market_value = (
                    qty * current_price
                )

                unrealized_pnl = (
                    (current_price - avg_cost)
                    * qty
                )

                unrealized_pct = (
                    (current_price - avg_cost)
                    / avg_cost
                ) * 100

                total_market_value += (
                    market_value
                )

                total_unrealized_pnl += (
                    unrealized_pnl
                )

                lines = [

                    "",

                    symbol,

                    f"   Qty:          {qty}",

                    f"   Avg Cost:     ${avg_cost:.2f}",

                    f"   Current:      ${current_price:.2f}",

                    f"   Market Value: ${market_value:.2f}",

                    f"   Unrealized:   ${unrealized_pnl:.2f} ({unrealized_pct:.2f}%)"

                ]

                for line in lines:

                    print(line)

                    report.append(line)

        line = ""
        print(line)
        report.append(line)

        line = "=" * 50
        print(line)
        report.append(line)

        line = "SUMMARY"
        print(line)
        report.append(line)

        line = "=" * 50
        print(line)
        report.append(line)

        line = (
            f"Position Value: "
            f"${total_market_value:,.2f}"
        )

        print(line)
        report.append(line)

        line = (
            f"Unrealized PnL: "
            f"${total_unrealized_pnl:,.2f}"
        )

        print(line)
        report.append(line)

        line = (
            f"Remaining Cash: "
            f"${cash:,.2f}"
        )

        print(line)
        report.append(line)

        line = (
            f"Total Equity: "
            f"${equity:,.2f}"
        )

        print(line)
        report.append(line)

        save_status_report(
            "\n".join(report)
        )

        print()
        print(
            f"Status saved: "
            f"{status_file}"
        )

        client.disconnect()

        return

    buy_candidates = []

    trend_pass_count = 0
    
    momentum_pass_count = 0
    
    accel_pass_count = 0
    
    rvol_pass_count = 0
    
    pullback_pass_count = 0
    
    earnings_pass_count = 0
    
    earnings_removed = []

    order_preview = []

    skipped_orders = []

    planned_capital = 0

    already_owned = []

    owned_symbols = {
        p["symbol"]
        for p in positions
    }

    orders_to_submit = []

    exit_orders = []

    for symbol in WATCHLIST:

        try:
            
            bars = client.get_daily_bars(symbol)

            result = evaluate_symbol(bars)

            earnings_pass = earnings_ok(
                symbol
            )

            days_to_earnings = (
                get_days_to_earnings(
                    symbol
                )
            )

            strategy_signal = result["signal"]

            final_signal = (
                strategy_signal
                and earnings_pass
            )

            if earnings_pass:
                earnings_pass_count += 1

            if not earnings_pass:

                earnings_removed.append(
                    (
                        symbol,
                        days_to_earnings
                    )
                )
            
            if symbol in owned_symbols:

                position = position_lookup[
                    symbol
                ]

                already_owned.append({

                    "symbol": symbol,

                    "qty": position["qty"],

                    "avg_cost": position["avg_cost"]

                })

                final_signal = False

            if final_signal:

                buy_candidates.append({

                    "symbol": symbol,

                    "close": bars["close"].iloc[-1],

                    "score": result["score"],

                    "momentum": result["momentum"],

                    "acceleration": result["acceleration"],

                    "rvol": result["rvol"]

                })
                
            if result["trend_ok"]:
                trend_pass_count += 1

            if result["momentum_ok"]:
                momentum_pass_count += 1

            if result["accel_ok"]:
                accel_pass_count += 1

            if result["rvol_ok"]:
                rvol_pass_count += 1

            if result["pullback_ok"]:
                pullback_pass_count += 1            
                        
            print(
                f"{symbol:<6} "
                f"T={result['trend_ok']} "
                f"M={result['momentum_ok']} "
                f"A={result['accel_ok']} "
                f"R={result['rvol_ok']} "
                f"P={result['pullback_ok']} "
                f"PB={result['pullback_pct']:.2f}% "
                f"RV={result['rvol']:.2f} "
                f"E={earnings_pass} "
                f"DTE={days_to_earnings} "
                f"Strategy={strategy_signal} "
                f"Final={final_signal}"
            )
            
        except Exception as e:

            traceback.print_exc()
            raise

            print(
                f"{symbol:<6} "
                f"FAILED: {e}"
            )

    # ============================================================
    # KEEP ONLY TOP N CANDIDATES
    # ============================================================

    buy_candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    buy_candidates = buy_candidates[
        :MAX_NEW_PURCHASES_PER_DAY
    ]

    candidate_count = len(
        buy_candidates
    )

    scanner_quality = "NO SIGNALS"

    if buy_candidates:

        top_score = buy_candidates[0]["score"]

        if top_score >= 100:
            scanner_quality = "EXCEPTIONAL"

        elif top_score >= 75:
            scanner_quality = "STRONG"

        elif top_score >= 50:
            scanner_quality = "NORMAL"

        elif top_score >= 25:
            scanner_quality = "QUIET"

        else:
            scanner_quality = "VERY QUIET"

    # ============================================================
    # SAVE ONLY TOP N SIGNALS
    # ============================================================

    if RUN_MODE == "SCAN":

        for candidate in buy_candidates:

            save_pending_signal(

                symbol=candidate["symbol"],

                momentum=candidate["momentum"],

                acceleration=candidate["acceleration"],

                rvol=candidate["rvol"],

                score=candidate["score"]

            )

    print()
    print("=" * 50)
    print("WAVERIDER PAPER")
    print("=" * 50)

    print(f"Cash:   ${cash:,.2f}")
    print(f"Equity: ${equity:,.2f}")

    print(
        f"Scanner Quality: {scanner_quality}"
    )

    print()

    print("=" * 50)
    print("FILTER STATS")
    print("=" * 50)

    print(f"Trend Pass:        {trend_pass_count}")
    print(f"Momentum Pass:     {momentum_pass_count}")
    print(f"Acceleration Pass: {accel_pass_count}")
    print(f"RVOL Pass:         {rvol_pass_count}")
    print(f"Pullback Pass:     {pullback_pass_count}")
    print(f"Earnings Pass:     {earnings_pass_count}")

    print()

    print("=" * 50)
    print("POSITIONS")
    print("=" * 50)

    if not positions:

        print("None")

    else:

        for p in positions:

            print(
                f"{p['symbol']} "
                f"Qty={p['qty']} "
                f"AvgCost={p['avg_cost']:.2f}"
            )

    print()

    exit_candidates = []

    for p in positions:

        symbol = p["symbol"]

        bars = client.get_daily_bars(
            symbol
        )

        state = load_state()

        exit_result = evaluate_exit(
            bars,
            state.get(symbol)
        )

        if exit_result["exit_signal"]:

            exit_candidates.append({

                "symbol": symbol,

                "qty": p["qty"],

                "reason":
                    exit_result["reason"],

                "momentum":
                    exit_result.get("momentum"),

                "acceleration":
                    exit_result.get("acceleration"),

                "acceleration_yesterday":
                    exit_result.get("acceleration_yesterday"),

                "momentum_yesterday":
                    exit_result.get("momentum_yesterday"),
            })

            exit_orders.append({

                "symbol": symbol,

                "qty": p["qty"],

                "reason":
                    exit_result["reason"]

            })

    print()

    print("=" * 50)
    print("EXIT CANDIDATES")
    print("=" * 50)

    if not exit_candidates:

        print("None")

    else:

        for exit_candidate in exit_candidates:

            print()

            print(
                exit_candidate["symbol"]
            )

            print(
                f"   Reason: "
                f"{exit_candidate['reason']}"
            )

            if exit_candidate["momentum"] is not None:

                print(
                    f"   Momentum: "
                    f"{exit_candidate['momentum']:.2f}"
                )
            
            if exit_candidate["acceleration"] is not None:

                print(
                    f"   Acceleration: "
                    f"{exit_candidate['acceleration']:.2f}"
                )

            if exit_candidate["acceleration_yesterday"] is not None:

                print(
                    f"   Accel Yesterday: "
                    f"{exit_candidate['acceleration_yesterday']:.2f}"
                )

            if exit_candidate["momentum_yesterday"] is not None:

                print(
                    f"   Momentum Yesterday: "
                    f"{exit_candidate['momentum_yesterday']:.2f}"
                )

    print()
    print("=" * 50)
    print("EXIT ORDER PREVIEW")
    print("=" * 50)

    if not exit_orders:

        print("None")

    else:

        for order in exit_orders:

            print()

            print(
                f"SELL {order['symbol']}"
            )

            print(
                f"Qty: {order['qty']}"
            )

            print(
                f"Reason: {order['reason']}"
            )

    print()
    print("=" * 50)
    print("BUY CANDIDATES")
    print("=" * 50)

    print()
    print(
        f"Market Environment: {scanner_quality}"
    )

    available_cash = cash
    
    available_cash = cash

    if not buy_candidates:

        print("None")

    else:

        for rank, candidate in enumerate(
            buy_candidates,
            start=1
        ):

            percentile = (
                (candidate_count - rank + 1)
                / candidate_count
            ) * 100
            
            shares = int(
                POSITION_SIZE
                / candidate["close"]
            )

            position_cost = (
                shares
                * candidate["close"]
            )

            if position_cost > available_cash:

                skipped_orders.append({

                    "symbol": candidate["symbol"],

                    "reason": "Insufficient Cash"

                })

                continue

            available_cash -= position_cost

            if shares > 0:

                planned_capital += position_cost

                order_preview.append({

                    "symbol": candidate["symbol"],

                    "shares": shares,

                    "cost": position_cost

                })

                order = {

                    "symbol": candidate["symbol"],

                    "shares": shares,

                    "price": candidate["close"],

                    "score": candidate["score"],

                    "momentum": candidate["momentum"],

                    "acceleration": candidate["acceleration"],

                    "rvol": candidate["rvol"],

                    "order_type": "MKT",

                    "action": "BUY"

                }

                orders_to_submit.append(
                    order
                )

            else:

                skipped_orders.append({

                    "symbol": candidate["symbol"],

                    "reason": "Position Size Too Small"

                })
            
            #print()

            if rank > 1:

                previous_score = buy_candidates[
                    rank - 2
                ]["score"]

                current_score = candidate["score"]

                gap_from_previous = (
                    previous_score
                    - current_score
                )

                if gap_from_previous > 50:

                    print()
                    print("-" * 50)
                    print("NEW TIER")
                    print("-" * 50)

            score = candidate["score"]

            if score >= 100:
                label = "EXCEPTIONAL"

            elif score >= 75:
                label = "STRONG"

            elif score >= 50:
                label = "NORMAL"

            elif score >= 25:
                label = "WEAK"

            else:
                label = "VERY WEAK"

            print(
                f"{rank}. "
                f"{candidate['symbol']} "
                f"[{label}]"
            )


            print(
                f"   Price:        "
                f"${candidate['close']:.2f}"
            )

            print(
                f"   Shares:       "
                f"{shares}"
            )

            print(
                f"   Position:     "
                f"${position_cost:.2f}"
            )

            if shares == 0:

                print(
                    "   WARNING:      "
                    "Position Size Too Small"
                )

            print(
                f"   Momentum:     "
                f"{candidate['momentum']:.2f}"
            )

            print(
                f"   Acceleration: "
                f"{candidate['acceleration']:.2f}"
            )

            print(
                f"   RVOL:         "
                f"{candidate['rvol']:.2f}"
            )
            
            print(
                f"   Score:        "
                f"{candidate['score']:.2f}"
            )

            if rank < candidate_count:

                next_score = buy_candidates[
                    rank
                ]["score"]

                gap = (
                    candidate["score"]
                    - next_score
                )

                print(
                    f"   Gap To Next:  "
                    f"{gap:.2f}"
                )

    print()
    print("=" * 50)
    print("TRADE PLAN")
    print("=" * 50)

    remaining_cash = (
        cash
        - planned_capital
    )

    if cash > 0:
        capital_utilization = (
            planned_capital / cash
        ) * 100
    else:
        capital_utilization = 0

    remaining_slots = int(
        remaining_cash
        / POSITION_SIZE
    )

    print(
        f"Cash Available:     "
        f"${cash:,.2f}"
    )

    print(
        f"Planned Capital:    "
        f"${planned_capital:,.2f}"
    )

    print(
        f"Remaining Slots:     "
        f"{remaining_slots}"
    )

    print(
        f"Remaining Cash:     "
        f"${remaining_cash:,.2f}"
    )

    print(
        f"Capital Utilization: "
        f"{capital_utilization:.1f}%"
    )

    print(
        f"Planned Orders:     "
        f"{len(order_preview)}"
    )
      
    print()
    print("=" * 50)
    print("SKIPPED ORDERS")
    print("=" * 50)

    if not skipped_orders:

        print("None")

    else:

        for order in skipped_orders:

            print(
                f"{order['symbol']} "
                f"- "
                f"{order['reason']}"
            )

    print()
    print("=" * 50)
    print("ALREADY OWNED")
    print("=" * 50)

    if not already_owned:

        print("None")

    else:

        for position in already_owned:

            print()

            print(
                position["symbol"]
            )

            print(
                f"   Qty:      "
                f"{position['qty']}"
            )

            print(
                f"   Avg Cost: "
                f"${position['avg_cost']:.2f}"
            )

    print()
    print("=" * 50)
    print("REMOVED BY EARNINGS")
    print("=" * 50)

    if not earnings_removed:

        print("None")

    else:

        for symbol, days in earnings_removed:

            print(
                f"{symbol:<6} "
                f"({days} days)"
            )

    if RUN_MODE == "SCAN":

        print()
        print("=" * 50)
        print("SIGNALS SAVED")
        print("=" * 50)

        print(
            f"Signals queued: "
            f"{len(buy_candidates)}"
        )

        print()

        client.disconnect()

        return

    print()
    print("=" * 50)
    print("ORDERS TO SUBMIT")
    print("=" * 50)
    print()

    for order in orders_to_submit:

        print(
            f"{order['action']} "
            f"{order['symbol']} "
            f"Qty={order['shares']} "
            f"{order['order_type']}"
        )

    if ENABLE_PAPER_ORDERS:

        print()
        print("=" * 50)
        print("ORDER SUBMISSION")
        print("=" * 50)

        for order in orders_to_submit:

            print(
                f"Sending BUY "
                f"{order['symbol']} "
                f"Qty={order['shares']}"
            )

            trade = client.place_market_buy(
                order["symbol"],
                order["shares"]
            )

            client.ib.sleep(2)

            fill_price = trade.orderStatus.avgFillPrice

            if fill_price == 0 and trade.fills:
                fill_price = trade.fills[-1].execution.price

            state = load_state()

            state[order["symbol"]] = {

                "entry_date":
                    pd.Timestamp.now()
                    .strftime("%Y-%m-%d"),

                "entry_price":
                    fill_price,

                "highest_close":
                    fill_price,

                "max_momentum":
                    order["momentum"],

                "max_favorable_pct":
                    0
            }

            save_state(state)

            log_trade(
                symbol=order["symbol"],
                action="BUY",
                shares=order["shares"],
                price=fill_price,
                score=order["score"]
            )

        for order in exit_orders:

            position = position_lookup[
                order["symbol"]
            ]
            
            print(
                f"Sending SELL "
                f"{order['symbol']} "
                f"Qty={order['qty']}"
            )

            trade = client.place_market_sell(
                order["symbol"],
                order["qty"]
            )

            client.ib.sleep(2)

            exit_price = trade.orderStatus.avgFillPrice

            if exit_price == 0 and trade.fills:
                exit_price = trade.fills[-1].execution.price

            entry_price = (
                position["avg_cost"]
            )

            shares = (
                position["qty"]
            )

            pnl_dollars = (
                exit_price - entry_price
            ) * shares

            pnl_percent = (
                (exit_price - entry_price)
                / entry_price
            ) * 100

            print(
                f"Entry: ${entry_price:.2f}"
            )

            print(
                f"Exit:  ${exit_price:.2f}"
            )

            print(
                f"PnL: ${pnl_dollars:.2f} "
                f"({pnl_percent:.2f}%)"
            )

            state.pop(
                order["symbol"],
                None
            )

            save_state(state)

            log_trade(

                symbol=order["symbol"],

                action="SELL",

                shares=shares,

                price=exit_price,

                score=0,

                pnl_dollars=pnl_dollars,

                pnl_percent=pnl_percent,

                exit_reason=order["reason"]

            )

    else:

        print()
        print("=" * 50)
        print("ORDER SUBMISSION")
        print("=" * 50)

        print(
            "Paper Orders Disabled"
        )

    print()
    print(
        f"Total Orders: "
        f"{len(orders_to_submit)}"
    )    

    client.disconnect()

if __name__ == "__main__":

    main()