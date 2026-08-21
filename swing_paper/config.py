BOT_VERSION = "1.0"

# ============================================================
# ORDER SETTINGS
# ============================================================

ENABLE_PAPER_ORDERS = True

ENABLE_MORNING_CONFIRMATION = True

# RUN_MODE = "SCAN"

RUN_MODE = "CONFIRM"

# ============================================================
# IBKR CONNECTION
# ============================================================

HOST = "127.0.0.1"

PORT = 7497          # Paper Account
# PORT = 7496        # Live Account

CLIENT_ID = 1

# ============================================================
# POSITION SIZING
# ============================================================

POSITION_SIZE = 2500

MAX_OPEN_POSITIONS = 20

MAX_NEW_PURCHASES_PER_DAY = 3

# ============================================================
# WATCHLIST
# ============================================================

WATCHLIST = [

    # ========================================
    # BIG TECH / AI
    # ========================================

    "AAPL",
    "MSFT",
    "NVDA",
    "AMD",
    "META",
    "AMZN",
    "GOOGL",
    "GOOG",
    "TSLA",
    "NFLX",

    # ========================================
    # HIGH GROWTH / MOMENTUM
    # ========================================

    "PLTR",
    "SNOW",
    "SHOP",
    "RBLX",
    "SOFI",
    "ARM",
    "UBER",
    "ABNB",
    "HIMS",
    "APP",
    "CELH",
    "CAVA",
    "DUOL",
    "RKLB",
    "HOOD",
    "COIN",

    # ========================================
    # ENTERPRISE / SOFTWARE
    # ========================================

    "CRM",
    "NOW",
    "INTU",
    "ADBE",
    "ORCL",
    "PANW",
    "MDB",
    "NET",
    "DDOG",
    "CRWD",
    "ZS",
    "FTNT",
    "CDNS",
    "SNPS",
    "ADSK",
    "VRSN",

    # ========================================
    # SEMICONDUCTORS
    # ========================================

    "ANET",
    "DELL",
    "QCOM",
    "INTC",
    "MU",
    "AMAT",
    "LRCX",
    "KLAC",
    "ASML",

    # ========================================
    # FINANCIALS
    # ========================================

    "JPM",
    "GS",
    "MS",
    "BLK",
    "BX",
    "KKR",
    "APO",
    "ARES",
    "SCHW",
    "CME",
    "ICE",
    "SPGI",
    "MCO",
    "V",
    "MA",
    "PYPL",
    "AXP",

    # ========================================
    # CONSUMER / RETAIL
    # ========================================

    "COST",
    "WMT",
    "HD",
    "LULU",
    "MCD",
    "CMG",
    "BKNG",

    # ========================================
    # HEALTHCARE
    # ========================================

    "LLY",
    "UNH",
    "ABBV",
    "ISRG",
    "BSX",
    "SYK",
    "TMO",
    "DHR",
    "VRTX",
    "REGN",

    # ========================================
    # INDUSTRIALS / DEFENSE
    # ========================================

    "CAT",
    "GE",
    "DE",
    "ETN",
    "PH",
    "TT",
    "PWR",
    "HON",
    "URI",
    "LMT",
    "RTX",
    "NOC",
    "GD",
    "BA",

    # ========================================
    # ENERGY / MATERIALS
    # ========================================

    "XOM",
    "CVX",
    "COP",
    "EOG",
    "SLB",
    "MPC",
    "VLO",
    "LIN",
    "NUE",
    "FCX",

    # ========================================
    # SPECIALTY / QUALITY
    # ========================================

    "TTWO",
    "FICO",
    "ODFL"
]

# ============================================================
# LOGGING
# ============================================================

ENABLE_LOGGING = True

# ============================================================
# FILES
# ============================================================

POSITIONS_FILE = "state/positions.json"

ACCOUNT_FILE = "state/account.json"

# ============================================================
# STRATEGY FILTERS
# ============================================================

MIN_MOMENTUM = 5.0

MIN_ACCELERATION = 0.1

MIN_RVOL = 0.95

PULLBACK_LOOKBACK_DAYS = 5

MAX_PULLBACK_PCT = 1.0 #0.05

# ============================================================
# EARNINGS FILTER
# ============================================================

ENABLE_EARNINGS_FILTER = True

EARNINGS_BLACKOUT_DAYS = 7

# ============================================================
# EXITS
# ============================================================

ENABLE_TIME_STOP_EXIT = True

MAX_HOLD_DAYS = 10

ENABLE_MOMENTUM_FAILURE_EXIT = True

MOMENTUM_DECAY_THRESHOLD = 60

ENABLE_MOMENTUM_COLLAPSE_EXIT = True

ENABLE_LOST_20_SMA_EXIT = True

SMA20_EXIT_BUFFER_PCT = 0.01

ENABLE_TRAIL_STOP_EXIT = True

EMERGENCY_STOP_PCT = 0.10

ENABLE_PROFIT_GIVEBACK_EXIT = True

PROFIT_PROTECT_TRIGGER = 10.0

MAX_PROFIT_GIVEBACK = 5.0

# ============================================
# AUTO MODE
# ============================================

AUTO_MODE = True

MINIMUM_BUY_SCORE = 15

EXIT_HOUR = 6
EXIT_MINUTE = 50

BUY_HOUR = 7
BUY_MINUTE = 12

LOOP_SLEEP_SECONDS = 30






