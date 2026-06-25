"""
Download 1 year of MES 15-min RTH bars from IB into data/mes_15min.csv.

Requires IB Gateway running on 127.0.0.1:7497 (paper account is fine).

Usage:
    python scripts/download_history.py
"""

import csv
import pathlib
import pytz
from ib_insync import IB, Future

ET = pytz.timezone("US/Eastern")

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=10)

# Qualify front-month contract — pick nearest expiry still active
from datetime import datetime
contract = Future(symbol="MES", exchange="CME", currency="USD")
details  = ib.reqContractDetails(contract)
today    = datetime.now().strftime("%Y%m%d")
active   = sorted(
    [d for d in details if d.contract.lastTradeDateOrContractMonth >= today],
    key=lambda d: d.contract.lastTradeDateOrContractMonth,
)
mes = active[0].contract
print(f"Contract : {mes.localSymbol}  expiry={mes.lastTradeDateOrContractMonth}")

# IB paper accounts cap at ~6 months per request for 15-min bars.
# Make two requests with different end dates and combine, deduplicating by timestamp.
import time as _time

def fetch_chunk(end_dt):
    chunk = ib.reqHistoricalData(
        mes,
        endDateTime=end_dt,
        durationStr="1 Y",
        barSizeSetting="15 mins",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=2,
    )
    _time.sleep(10)  # IB pacing: wait between requests
    return chunk

chunk1 = fetch_chunk("")                          # most recent 1 year
chunk2 = fetch_chunk(chunk1[0].date.strftime("%Y%m%d %H:%M:%S") if chunk1 else "")  # year before that

all_bars = {b.date: b for b in list(chunk2) + list(chunk1)}  # chunk1 wins on overlap
bars = [all_bars[k] for k in sorted(all_bars)]
print(f"Downloaded {len(chunk1)} + {len(chunk2)} bars = {len(bars)} total (deduplicated)")

pathlib.Path("data").mkdir(exist_ok=True)
out = pathlib.Path("data/mes_15min.csv")

with open(out, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["date", "open", "high", "low", "close", "volume"])
    for bar in bars:
        # Convert UTC → ET so market-hours strings ("09:45", "15:30") match
        dt_et = bar.date.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([dt_et, bar.open, bar.high, bar.low, bar.close, bar.volume])

print(f"Saved {len(bars)} bars to {out}")
ib.disconnect()
