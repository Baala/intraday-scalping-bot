# IB Gateway Setup (Paper Trading)

## Step 1 — Create IB Account

1. Go to interactivebrokers.com → Open Account → Individual
2. Paper trading account is automatically included (no deposit needed)
3. Log in to Client Portal → switch to **Paper Trading** mode in the top menu

## Step 2 — Download IB Gateway

- Lightweight option (recommended): ibkr.com/en/trading/ibgateway.html
- Full TWS desktop app: download.interactivebrokers.com/installers/tws/latest
- Install and launch; log in with your paper trading credentials

## Step 3 — Configure API Access

1. In IB Gateway: **File → Global Configuration → API → Settings**
2. Check: **"Enable ActiveX and Socket Clients"**
3. Set port to **7497** (paper trading) — never use 7496 (live account)
4. Uncheck: **"Read-Only API"**
5. Add `127.0.0.1` to trusted IPs if prompted
6. Click **Apply** and restart IB Gateway

## Step 4 — Install Python Dependencies

```bash
pip install ib_insync pytz
```

## Step 5 — Test Connection

```python
from ib_insync import IB
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)
print(ib.accountValues())   # should print paper account balance
ib.disconnect()
```

If this prints account values, you're connected. If it errors, check Step 3.

## Step 6 — Find the Active MES Contract

MES rolls quarterly (Mar/Jun/Sep/Dec). To find the current front-month:

```python
from ib_insync import IB, Future
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)
details = ib.reqContractDetails(Future('MES', 'CME'))
for d in details:
    print(d.contract.localSymbol, d.contract.lastTradeDateOrContractMonth)
ib.disconnect()
```

The script `ib_futures_stream.py` qualifies the contract automatically — no manual input needed.

## Port Reference

| Mode | TWS Port | IB Gateway Port |
|------|----------|-----------------|
| Paper trading | **7497** | 4002 |
| Live trading | 7496 | 4001 |

## Common Issues

| Error | Fix |
|-------|-----|
| "Connection refused" | IB Gateway not running, or wrong port (use 7497) |
| "Max clients reached" | Change `clientId` in config (try 2, 3, 4) |
| "No security definition" | MES contract expired — script auto-qualifies, just restart |
| Bot freezes on signal | Ensure using `asyncio.create_subprocess_exec` not `subprocess.run` |
