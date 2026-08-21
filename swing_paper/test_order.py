from ibkr_client import IBKRClient

client = IBKRClient()

client.connect()

client.place_market_buy(
    "AAPL",
    1
)

client.disconnect()