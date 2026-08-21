import yfinance as yf


def get_current_price(symbol):

    ticker = yf.Ticker(symbol)

    data = ticker.history(
        period="1d",
        interval="1m"
    )

    if data.empty:

        return None

    return float(
        data["Close"].iloc[-1]
    )


def get_today_open(symbol):

    ticker = yf.Ticker(symbol)

    data = ticker.history(
        period="1d",
        interval="1m"
    )

    if data.empty:

        return None

    return float(
        data["Open"].iloc[0]
    )