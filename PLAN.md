# Intraday Scalping Bot — IB TWS + MES Futures

## Context

Phase 3 of the trading bot project. The daily swing bot (trading-bot repo) uses Alpaca + yfinance on daily bars. This new repo is for real-time intraday scalping of MES (Micro S&P 500 Futures) via Interactive Brokers TWS API. The C++ RiskManager from trading-bot is reused — only the execution layer changes (IB ib_insync vs Alpaca REST).

**MES specs:**
- 1 index point = $5.00 value
- Tick size = 0.25 points = $1.25 per tick
- Paper trading port: 7497 (TWS) or 4002 (IB Gateway)
- PDT rule does NOT apply (futures)

**Risk parameters:**
- Capital: $1,000 (paper trading)
- Risk: 1% = $10/trade
- Stop: 2 points below entry ($10 = 2 pts × $5/pt)
- Target: 4 points above entry (2:1 RR = $20 gross profit)
- Commission: $0.85/side = $1.70 round trip → net win $18.30, net loss $11.70, effective RR 1.56
- Max contracts: 1 (MES margin ~$500–600/contract)

---

## Monthly P&L Estimates

4 trades/day average, 20 trading days, $1.70 commission/round trip:

| Scenario | Win Rate | Monthly P&L |
|----------|----------|-------------|
| Best (trending market) | 60% | +$440 |
| Realistic | 45% | +$80 |
| Break-even | 42% | $0 |
| Worst (CB fires daily) | 25% | −$300 max |

Circuit breaker caps worst day at $30 loss (3% of capital). Unlimited trades/day.

---

## Repo Structure

```
intraday-scalping-bot/
├── README.md
├── PLAN.md                          ← this file
├── config/
│   └── scalping_config.json
├── paper_trading/
│   └── ib_futures_stream.py
├── core/
│   ├── risk/
│   │   ├── RiskManager.h
│   │   └── RiskManager.cpp
│   └── data/
│       └── OHLCVBar.h
└── docs/
    └── ib_gateway_setup.md
```

---

## Key Technical Decisions

### 1. Async Subprocess (Critical)
`ib_futures_stream.py` uses `asyncio.create_subprocess_exec()` to call the C++ risk binary.
**Never use `subprocess.run()`** — it blocks the asyncio event loop and freezes the IB WebSocket, causing missed bars.

Pattern:
```python
# on_bar_update() is sync — cannot await
def on_bar_update(...):
    asyncio.ensure_future(handle_buy_signal(...))   # schedules on event loop

# handle_buy_signal() is async — can safely await C++
async def handle_buy_signal(...):
    risk = await call_cpp_risk(price)               # non-blocking
    ib.placeOrder(...)
```

### 2. Commission in Backtesting
When adapting `BacktestEngine.cpp` for 1-min MES data:
```
commissionPct = (1.70 / (entryPrice * 5.0)) * 100
```
Effective RR after commission: $18.30 / $11.70 = 1.56 (not 2.0).

### 3. Circuit Breaker
`daily_pnl <= -30.0` → `compute_contracts()` returns 0, no new trades.
Checked before each BUY signal. Max loss per day ≈ $36.75 (3 losses × $12.25).

### 4. Bar Aggregation
IB only streams 5-second real-time bars. Script counts 12 of them to form one 1-minute bar:
```python
if bar_count % 12 != 0:
    return   # wait for full 1-min bar
```

---

## What's NOT in Phase 3 (Future Work)

- Backtesting on 1-min historical MES data (requires IB historical data pull)
- C++ signal engine in the hot path (Python MA is fast enough for 1-min bars)
- Live account trading (paper validate for at least 2 weeks first)
- Multiple contracts / dynamic sizing (needs $5k+ capital for margin safety)
- ATR-based stop instead of fixed 2-point stop
