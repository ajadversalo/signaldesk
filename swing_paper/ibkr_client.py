# ibkr_paper/ibkr_client.py

from ib_insync import (
    IB,
    Stock,
    MarketOrder,
    LimitOrder
)

from config import (
    HOST,
    PORT,
    CLIENT_ID,
    ENABLE_MORNING_CONFIRMATION
)


class IBKRClient:

    def __init__(self):

        self.ib = IB()

    def connect(
        self,
        client_id=None
    ):

        if self.ib.isConnected():

            print("Already connected.")

            return

        self.ib.connect(
            HOST,
            PORT,
            clientId=(
                client_id
                if client_id is not None
                else CLIENT_ID
            )
        )

        actual_client_id = (
            client_id
            if client_id is not None
            else CLIENT_ID
        )

        print(
            f"Connected to IBKR "
            f"(Port={PORT}, ClientId={actual_client_id})"
        )

    def disconnect(self):

        if self.ib.isConnected():

            self.ib.disconnect()

            print("Disconnected.")

            print()

    def get_cash(self):

        summary = self.ib.accountSummary()

        for item in summary:

            if (
                item.tag == "TotalCashBalance"
                and item.currency == "USD"
            ):

                return float(item.value)

        return 0.0

    def get_equity(self):

        summary = self.ib.accountSummary()

        for item in summary:

            if (
                item.tag == "NetLiquidationByCurrency"
                and item.currency == "USD"
            ):

                return float(item.value)

        return 0.0

    def get_positions(self):

        positions = []

        for p in self.ib.positions():

            positions.append({

                "symbol":
                    p.contract.symbol,

                "qty":
                    p.position,

                "avg_cost":
                    p.avgCost

            })

        return positions
    
    def place_market_buy(
        self,
        symbol,
        quantity
    ):

        contract = Stock(
            symbol,
            "SMART",
            "USD"
        )

        self.ib.qualifyContracts(
            contract
        )

        if not ENABLE_MORNING_CONFIRMATION:

            print("AFTER HOURS ENABLED")

            bars = self.get_daily_bars(
                symbol
            )

            limit_price = round(
                bars["close"].iloc[-1] * 1.02,
                2
            )

            order = LimitOrder(
                "BUY",
                quantity,
                limit_price
            )

            order.outsideRth = True

            print(
                f"After Hours Limit: "
                f"${limit_price}"
            )

        else:

            order = MarketOrder(
                "BUY",
                quantity
            )

        trade = self.ib.placeOrder(
            contract,
            order
        )

        print(
            f"Submitted BUY "
            f"{quantity} "
            f"{symbol} "
            f"(OrderId={order.orderId})"
        )

        return trade
    
    def place_market_sell(
        self,
        symbol,
        quantity
    ):

        contract = Stock(
            symbol,
            "SMART",
            "USD"
        )

        self.ib.qualifyContracts(
            contract
        )

        if not ENABLE_MORNING_CONFIRMATION:

            print("AFTER HOURS SELL ENABLED")

            bars = self.get_daily_bars(
                symbol
            )

            limit_price = round(
                bars["close"].iloc[-1] * 0.98,
                2
            )

            order = LimitOrder(
                "SELL",
                quantity,
                limit_price
            )

            order.outsideRth = True

            print(
                f"After Hours Limit: "
                f"${limit_price}"
            )

        else:

            order = MarketOrder(
                "SELL",
                quantity
            )

        trade = self.ib.placeOrder(
            contract,
            order
        )

        print(
            f"Submitted SELL "
            f"{quantity} "
            f"{symbol} "
            f"(OrderId={order.orderId})"
        )

        return trade
    
    def get_daily_bars(self, symbol):

        from ib_insync import Stock
        import pandas as pd

        contract = Stock(
            symbol,
            "SMART",
            "USD"
        )

        self.ib.qualifyContracts(contract)

        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="1 Y",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True
        )

        return pd.DataFrame(bars)
        