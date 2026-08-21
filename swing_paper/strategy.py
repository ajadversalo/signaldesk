import pandas as pd

from config import (
    MIN_MOMENTUM,
    MIN_ACCELERATION,
    MIN_RVOL,
    PULLBACK_LOOKBACK_DAYS,
    MAX_PULLBACK_PCT,
    ENABLE_TIME_STOP_EXIT,
    MAX_HOLD_DAYS,
    ENABLE_MOMENTUM_FAILURE_EXIT,
    ENABLE_LOST_20_SMA_EXIT,
    SMA20_EXIT_BUFFER_PCT,
    ENABLE_MOMENTUM_COLLAPSE_EXIT,
    MOMENTUM_DECAY_THRESHOLD,
    ENABLE_TRAIL_STOP_EXIT,
    EMERGENCY_STOP_PCT,
    ENABLE_PROFIT_GIVEBACK_EXIT,
    PROFIT_PROTECT_TRIGGER,
    MAX_PROFIT_GIVEBACK,
)

def evaluate_symbol(df):

    sma20 = df["close"].rolling(20).mean().iloc[-1]
    sma50 = df["close"].rolling(50).mean().iloc[-1]
    sma200 = df["close"].rolling(200).mean().iloc[-1]

    close = df["close"].iloc[-1]

    momentum = (
        close / df["close"].iloc[-21] - 1
    ) * 100

    trend_ok = (
        close > sma20
        and sma20 > sma50
        and sma50 > sma200
    )

    momentum_ok = (
        momentum > MIN_MOMENTUM
    )

    momentum_now = (
        close / df["close"].iloc[-21] - 1
    ) * 100

    momentum_yesterday = (
        df["close"].iloc[-2]
        / df["close"].iloc[-22]
        - 1
    ) * 100

    momentum_ok = (
        momentum_now >= momentum_yesterday
    )

    acceleration = (
        momentum_now
        - momentum_yesterday
    )
    
    accel_ok = (
        acceleration > MIN_ACCELERATION
    )

    avg_volume = df["volume"].rolling(20).mean().iloc[-1]

    rvol = (
        df["volume"].iloc[-1]
        / avg_volume
    )

    rvol_ok = (
        rvol > MIN_RVOL
    )

    recent_high = (
        df["close"]
        .iloc[-(PULLBACK_LOOKBACK_DAYS + 1):-1]
        .max()
    )

    pullback_pct = (
        recent_high - close
    ) / recent_high

    pullback_ok = (
        pullback_pct <= MAX_PULLBACK_PCT
    )

    score = (
        momentum
        + acceleration
        + rvol
    )

    signal = (
        trend_ok
        and momentum_ok
        and accel_ok
        and rvol_ok
    )

    return {
        "trend_ok": trend_ok,
        "momentum_ok": momentum_ok,
        "accel_ok": accel_ok,
        "momentum": float(momentum_now),
        "acceleration": float(acceleration),
        "rvol": float(rvol),
        "rvol_ok": rvol_ok,
        "pullback_ok": pullback_ok,
        "pullback_pct": pullback_pct * 100,
        "signal": bool(signal),
        "score": float(score)
    }

def confirm_entry(df):

    close = df["close"].iloc[-1]

    prev_close = df["close"].iloc[-2]

    momentum_now = (
        close / df["close"].iloc[-21] - 1
    ) * 100

    momentum_prev = (
        df["close"].iloc[-2]
        / df["close"].iloc[-22]
        - 1
    ) * 100

    acceleration = (
        momentum_now
        - momentum_prev
    )

    close_ok = (
        close > prev_close
    )

    momentum_ok = (
        momentum_now >= momentum_prev
    )

    accel_ok = (
        acceleration > 0
    )

    confirmed = (
        close_ok
        and momentum_ok
        and accel_ok
    )

    return {
        "confirmed": confirmed,
        "close_ok": close_ok,
        "momentum_ok": momentum_ok,
        "accel_ok": accel_ok
    }

def evaluate_exit(
    df,
    position_state=None
):
    
    hold_days = 0

    if position_state:

        entry_date = pd.to_datetime(
            position_state["entry_date"]
        )

        hold_days = (
            pd.Timestamp.now()
            - entry_date
        ).days

        if (
            ENABLE_TIME_STOP_EXIT
            and hold_days >= MAX_HOLD_DAYS
        ):
            return {

                "exit_signal": True,

                "reason": "TIME_STOP",

                "hold_days": hold_days
            }

    momentum_today = (
        df["close"].iloc[-1]
        / df["close"].iloc[-21]
        - 1
    ) * 100

    max_momentum = 0

    if position_state:

        max_momentum = position_state.get(
            "max_momentum",
            momentum_today
        )

        max_momentum = max(
            max_momentum,
            momentum_today
        )

    close = df["close"].iloc[-1]

    entry_price = close

    max_favorable_pct = 0

    if position_state:

        entry_price = position_state.get(
            "entry_price",
            close
        )

        max_favorable_pct = position_state.get(
            "max_favorable_pct",
            0
        )

    current_gain_pct = (

        (close - entry_price)

        / entry_price

    ) * 100

    max_favorable_pct = max(
        max_favorable_pct,
        current_gain_pct
    )

    highest_close = close

    if position_state:

        highest_close = position_state.get(
            "highest_close",
            close
        )

        highest_close = max(
            highest_close,
            close
        )

    trail_stop = (

        highest_close

        * (1 - EMERGENCY_STOP_PCT)

    )

    if (
        ENABLE_TRAIL_STOP_EXIT
        and
        close < trail_stop
    ):
        return {

            "exit_signal": True,

            "reason": "TRAIL_STOP",

            "close": close,

            "trail_stop": trail_stop
        }
    
    if (
        ENABLE_PROFIT_GIVEBACK_EXIT
        and
        max_favorable_pct
        >= PROFIT_PROTECT_TRIGGER
        and
        current_gain_pct
        <
        (
            max_favorable_pct
            - MAX_PROFIT_GIVEBACK
        )
    ):
        return {

            "exit_signal": True,

            "reason": "PROFIT_GIVEBACK",

            "current_gain_pct":
                current_gain_pct,

            "max_favorable_pct":
                max_favorable_pct
        }

    sma20 = (
        df["close"]
        .rolling(20)
        .mean()
        .iloc[-1]
    )

    if (
        ENABLE_LOST_20_SMA_EXIT
        and
        close <
        (
            sma20
            * (1 - SMA20_EXIT_BUFFER_PCT)
        )
    ):
        return {

            "exit_signal": True,

            "reason": "LOST_20_SMA",

            "close": close,

            "sma20": sma20
        }
    
    momentum_yesterday = (
        df["close"].iloc[-2]
        / df["close"].iloc[-22]
        - 1
    ) * 100

    momentum_decay_pct = 0

    if max_momentum > 0:

        momentum_decay_pct = (

            (max_momentum - momentum_today)

            / max_momentum

        ) * 100

    momentum_2_days_ago = (
        df["close"].iloc[-3]
        / df["close"].iloc[-23]
        - 1
    ) * 100

    acceleration_today = (
        momentum_today
        - momentum_yesterday
    )

    acceleration_yesterday = (
        momentum_yesterday
        - momentum_2_days_ago
    )

    if (
        ENABLE_MOMENTUM_COLLAPSE_EXIT
        and
        momentum_decay_pct >=
        MOMENTUM_DECAY_THRESHOLD
    ):
        return {

            "exit_signal": True,

            "reason": "MOMENTUM_COLLAPSE",

            "momentum": momentum_today,

            "max_momentum": max_momentum,

            "momentum_decay_pct":
                momentum_decay_pct
        }

    if (
        ENABLE_MOMENTUM_FAILURE_EXIT
        and momentum_today < 0
    ):
        return {

            "exit_signal": True,

            "reason": "MOMENTUM_FAILURE",

            "momentum": momentum_today
        }

    exit_signal = (

        acceleration_today
        < (acceleration_yesterday * 0.50)

        and

        momentum_today
        < momentum_yesterday

    )

    return {

        "exit_signal":
            exit_signal,

        "reason":
            "ACCELERATION_DECAY",

        "momentum":
            momentum_today,

        "acceleration":
            acceleration_today,

        "acceleration_yesterday":
            acceleration_yesterday

    }