# Swing Scanner

A standalone, read-only momentum scanner extracted from `swing_paper`. It has no
broker connection, scheduler, portfolio state, or order-submission code.

Run from the repository root:

```powershell
python -m swing_scanner
```

The complete ranked report is written to `data/swing_scan.csv`; the console shows
the top three qualifying candidates. Supply an optional earnings calendar with:

```powershell
python -m swing_scanner --earnings-csv data/earnings/earnings_calendar.csv
```

The CSV must contain `Ticker` and `EarningsDate`. Without it, earnings are marked
unknown and allowed through so missing auxiliary data does not prevent scanning.

The clean baseline fixes the overwritten 5% momentum condition. Pullback remains
diagnostic by default because it was not part of the original effective signal;
set `REQUIRE_PULLBACK = True` in `config.py` to make it a gate.

