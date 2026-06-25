# IB Gateway Setup

## Step 1 — Create IB Account

1. Go to interactivebrokers.com → Open Account → Individual → **Margin account**
2. Trading permissions: select **Futures** (and Stocks if you plan grid trading)
3. Set futures experience to at least **1–2 years / Limited knowledge** — 0 experience gets denied
4. Select **IBKR PRO** (not Lite — futures commissions are lower on PRO)
5. Enroll in prediction markets to get the free $10 credit
6. Paper trading account is created automatically once approved (~1 business day)

## Step 2 — Find Your Paper Trading Credentials

1. Log in to Client Portal at clientportal.ibkr.com
2. Go to **User Menu (top right) → Paper Trading**
3. Note your paper account number (e.g. DU1234567)
4. **Login username**: your live account username (not the paper account ID)
5. **Password**: same as your live account

## Step 3 — Download and Launch IB Gateway

- Download: ibkr.com/en/trading/ibgateway.html (use the stable version)
- Launch, enter your **live username + password**
- Select **Paper Trading** from the environment dropdown before logging in

## Step 4 — Configure API Access

1. In IB Gateway: **File → Global Configuration → API → Settings**
2. ✅ Enable ActiveX and Socket Clients
3. Port: **7497**
4. ❌ Uncheck Read-Only API
5. ❌ Uncheck Auto logoff (critical — without this IB disconnects after inactivity, leaving bracket orders unmonitored)
6. Click **Apply** → restart IB Gateway → log back in to paper trading

## Step 5 — Test Connection

```python
from ib_insync import IB
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)
print(ib.accountValues())   # should print paper account balance (~$1,000,000)
ib.disconnect()
```

If this prints account values, you're ready. If it errors, check Step 4.

## Step 6 — Run the Bot

```
python main.py --mode paper
```

Dashboard: http://localhost:8080

## Port Reference

| Mode | IB Gateway Port |
|------|----------------|
| Paper trading | **7497** |
| Live trading | 4001 |

## Common Issues

| Error | Fix |
|-------|-----|
| "Connection refused" | IB Gateway not running, or wrong port |
| "Invalid username/password" | Use your live account username, not the paper account ID |
| "No security definition" | MES contract expired — bot auto-qualifies on restart |
| "Max clients reached" | Change `ib_client_id` in config to 2, 3, or 4 |
| Futures permission denied | Update trading experience from 0 to 1–2 years in account settings |
| Account type Cash | Contact IB support to change to Margin (explain it was a signup error) |
