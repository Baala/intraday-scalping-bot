# Intraday Scalping Bot — MES Futures (Phase 3)

Real-time intraday scalping bot for MES (Micro S&P 500 Futures) via Interactive Brokers TWS API.

## Architecture

```
CLI: python main.py --mode [paper|live]
    ↓  loads connection profile from config
IB Gateway (port from profile)
    ↓  ib_insync async WebSocket
bot/scalper.py  ← single bot, mode-agnostic logic
    ↓  reqHistoricalData(keepUpToDate=True) → 15-min bars
    ↓  EMA(5)/EMA(20) crossover + VWAP/ATR/ADX/volume filters
    ↓  asyncio.create_subprocess_exec (non-blocking)
C++ mes_risk binary
    ↓  {"contracts":2, "stop_loss":..., "take_profit":...}
Bracket order: MKT entry + SL stop + TP limit (OCA group)
    ↓  fill events → daily_pnl, trade log, state push
FastAPI WebSocket server (same asyncio loop)
    ↓
Browser dashboard — http://localhost:8080
```

## Risk Parameters

| Parameter | Value |
|-----------|-------|
| Capital | $5,000 |
| Risk/trade | $50 (1%) |
| Stop loss | 4 points ($20/contract) |
| Take profit | 8 points ($40/contract) |
| Max contracts (paper) | 2 |
| Max contracts (live) | 1 |
| Commission | $0.85/side = $1.70 round trip per contract |
| Entry slippage (market order) | 0.25 pt = $1.25/contract |
| SL exit slippage (stop → market) | 0.25 pt = $1.25/contract |
| Net win — per contract | $40 gross − $1.25 slip − $1.70 comm = **$37.05** |
| Net win — 2 contracts (paper) | **$74.10/trade** |
| Net loss — per contract | $20 gross + $1.25 entry + $1.25 exit + $1.70 comm = **$24.20** |
| Net loss — 2 contracts (paper) | **$48.40/trade** |
| Effective RR | 1.53 ($74.10 / $48.40) |
| Break-even win rate | 39.5% ($48.40 / $122.50) |
| Daily circuit breaker | $150 (3% of capital) — fires after ~3 losses at 2 contracts |
| Weekly circuit breaker | $200 (4% of capital) |

## Quick Start

1. Install IB Gateway — see [docs/ib_gateway_setup.md](docs/ib_gateway_setup.md)
2. `pip install ib_insync pytz fastapi uvicorn`
3. Build the C++ risk binary:
   ```
   cmake -B build && cmake --build build
   ```
4. Start IB Gateway on port 7497 (paper) or 7496 (live)
5. `python main.py --mode paper`
6. Open dashboard: `http://localhost:8080`

> **One-time IB setup:** Disable TWS auto-logoff under `Configure → API → Settings → uncheck "Auto logoff"`. Without this, TWS disconnects after inactivity and leaves open bracket orders unmonitored.

## Strategy

EMA(5)/EMA(20) crossover on 15-minute MES bars:
- **BUY** when EMA(5) crosses above EMA(20) **AND** price is above VWAP
- Exit when EMA(5) crosses below EMA(20), stop loss fills, take profit fills, or EOD sweep
- Trade hours: 09:45–15:30 ET (skip chaotic open; hard EOD close at 15:30)
- ~5–10 trades per month (high-quality signals only)

### Signal Filters (all must pass for a BUY)

| Filter | Condition | Reason |
|--------|-----------|--------|
| Market hours | 09:45–15:30 ET | Avoids open volatility and overnight gap risk |
| VWAP | Price > VWAP | Trade with institutional flow, not against it |
| ATR spike | Current TR ≤ 2× ATR(14) | Skips bars that are volatility outliers vs. the day's own baseline |
| ADX trend | ADX(14) ≥ 20 | EMA crossovers are noise in a ranging/choppy market |
| Volume | Bar volume ≥ 50% of 20-bar avg | Low volume = thin book = unreliable crossovers |
| SL cooldown | 30 market-minutes since last stop loss | Prevents re-entering the same whipsaw |
| Bar quality | ≥ 90% of expected bars received | Guards against stale indicators from data gaps |

### Circuit Breakers & Auto-Pause

| Condition | Action |
|-----------|--------|
| Daily loss ≥ $150 | Halt trading until next day |
| Weekly loss ≥ $200 | Halt trading until Monday |
| 5 consecutive stop losses | Auto-pause — manual resume required |
| Rolling win rate (20 trades) < 38% | Auto-pause — manual resume required |
| > 5 reconnects in a session | Auto-pause — manual resume required |
| ADX blocked > 70% of signals over 5 days | Auto-pause (choppy regime) — manual resume required |

## MES Contract Specs

- Exchange: CME
- 1 index point = $5.00
- Tick size: 0.25 points = $1.25
- Margin: ~$1,300/contract (paper trading)
- PDT rule: does NOT apply (futures)

## Monthly P&L Estimates

15-minute bars produce ~5–10 trades/month (fewer, higher-quality signals than 1-min). Estimates below use **7 trades/month** (midpoint) at **2 paper contracts**.

**Per-trade math:**
```
Win:  8 pts × $5 × 2 contracts = $80.00 gross
      − $1.25 entry slippage × 2 = −$2.50
      − $1.70 commission × 2     = −$3.40
                                  = $74.10 net

Loss: 4 pts × $5 × 2 contracts = $40.00 gross
      + $1.25 entry slippage × 2  = +$2.50
      + $1.25 SL exit slippage × 2 = +$2.50
      + $1.70 commission × 2      = +$3.40
                                  = $48.40 net
```

| Scenario | Win Rate | Wins | Losses | Monthly P&L |
|----------|----------|------|--------|-------------|
| Best (strong trend) | 55% | 3.85 | 3.15 | +$133 |
| Realistic | 50% | 3.5 | 3.5 | +$90 |
| Break-even | 39.5% | 2.77 | 4.23 | $0 |
| Poor (choppy market) | 30% | 2.1 | 4.9 | −$82 |

At 5 trades/month (slow): multiply by 5/7. At 10 trades/month (active): multiply by 10/7.

**Daily CB context:** Daily loss limit is $150. At 2 contracts, each loss costs $48.40 — the circuit breaker fires after 3 consecutive losses ($145.20) and halts trading for the day.

## Paper → Live Graduation Criteria

Never switch to live based on feel. Minimum thresholds:

| Criterion | Threshold |
|-----------|-----------|
| Paper trades completed | ≥ 200 |
| Win rate | ≥ 47% |
| Max single-week loss | ≤ $150 (weekly CB never fired) |
| Consecutive profitable weeks | ≥ 4 |
| Max drawdown | ≤ $750 (15% of paper capital) |

Live scaling: Month 1 at 1 contract → Month 2–3 at 2 contracts (if profitable).

## Stack

- Python 3.11+ / ib_insync 0.9+ / pytz / fastapi / uvicorn
- C++ risk engine (g++ 11+ / CMake 3.16+)
- Interactive Brokers paper account (free)

## File Structure

```
intraday-scalping-bot/
├── main.py                        entry point: python main.py --mode [paper|live]
├── CMakeLists.txt
├── config/scalping_config.json    all tuning params + connection profiles
├── include/nlohmann/json.hpp      single-header JSON lib (C++)
├── core/
│   ├── risk/RiskManager.h/.cpp    C++ position sizing
│   ├── backtest/BacktestEngine.h/.cpp
│   ├── mes_risk_main.cpp          CLI binary: build/mes_risk.exe
│   └── backtest_main.cpp          CLI binary: build/backtest.exe
├── bot/scalper.py                 trading logic (mode-agnostic)
├── dashboard/
│   ├── state.py                   shared BotState singleton
│   ├── server.py                  FastAPI app
│   └── static/index.html          web UI (Chart.js, live WebSocket)
├── data/                          runtime — created on first launch
│   ├── trades_paper.json
│   ├── trades_live.json
│   ├── bot_state_paper.json
│   └── regime_history_paper.json
└── docs/ib_gateway_setup.md
```

## Phase Roadmap

- **Phase 1** — Daily swing trading ([trading-bot](https://github.com/Baala/trading-bot), Alpaca)
- **Phase 2** — Enhanced signals + sentiment (trading-bot repo)
- **Phase 3** — THIS REPO — intraday scalping (IB TWS, MES futures)
- **Phase 4** — Live account, risk-scaled sizing ($5k+ capital)
