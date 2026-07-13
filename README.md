# Intraday Scalping Bot — MES Futures (Phase 3)

Real-time intraday scalping bot for MES (Micro S&P 500 Futures) via Interactive Brokers TWS API.

## Architecture

```
CLI: python main.py --mode [paper|live]
    ↓  _validate_config() — aborts on bad JSON params
IB Gateway (port from profile)
    ↓  ib_insync async WebSocket
bot/scalper.py  ← single bot, mode-agnostic logic
    ↓  seed 2D of RTH history → prime EMA / Wilder's ATR / ORB range
    ↓  reqHistoricalData(keepUpToDate=False) polled every 15-min boundary
    ↓  Signal 1: EMA(5/20) crossover + VWAP/ATR/ADX/volume filters
    ↓  Signal 2: ORB breakout (9:30–10:00 ET range, morning only)
    ↓  asyncio.create_subprocess_exec (non-blocking)
C++ mes_risk binary  ←  sl_points override: fixed 4pt (EMA) | 1×ATR (ORB)
    ↓  {"contracts":N, "stop_loss":..., "take_profit":...}
Bracket order: MKT entry + SL stop + TP limit (OCA group)
    ↓  fill events → daily_pnl, trade log, CSV append, state push
    ↓  EOD sweep 15:30 ET → cancel brackets → MKT close → P&L recovery
FastAPI WebSocket server (same asyncio loop)
    ↓
Browser dashboard — http://localhost:8080
```

## Risk Parameters

| Parameter | Value |
|-----------|-------|
| Capital | $5,000 |
| Risk/trade | $50 (1%) — hard cap enforced by C++ sizer |
| EMA stop loss | 4 points fixed |
| ORB stop loss | 1× Wilder's ATR(14) — min 4 pts; wider stop = fewer contracts |
| Take profit | 2× stop loss (2:1 RR) |
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
3. Build the C++ binaries (see [Build](#1-build-c-binaries) below)
4. Start IB Gateway on port 7497 (paper) or 7496 (live)
5. `py -3.11 main.py --mode paper`
6. Open dashboard: `http://localhost:8080`

> **One-time IB setup:** Disable TWS auto-logoff under `Configure → API → Settings → uncheck "Auto logoff"`. Without this, TWS disconnects after inactivity and leaves open bracket orders unmonitored.

---

## Step-by-Step Commands

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | **3.11** | `py -3.11 --version` — ib_insync incompatible with 3.12+ |
| g++ (MSYS2 ucrt64) | 11+ | `C:\msys64\ucrt64\bin\g++.exe` |
| IB Gateway | latest | Paper or live account, see [docs/ib_gateway_setup.md](docs/ib_gateway_setup.md) |

Install Python packages (run once):
```
py -3.11 -m pip install ib_insync pytz fastapi uvicorn
```

---

### 1. Build C++ Binaries

**Option A — VS Code (recommended):** Press `Ctrl+Shift+B` → runs `build/backtest.exe` build task.

**Option B — terminal:**
```
# From repo root
C:\msys64\ucrt64\bin\g++ -std=c++17 -O2 -Icore ^
    core/backtest_main.cpp ^
    core/backtest/BacktestEngine.cpp ^
    core/risk/RiskManager.cpp ^
    -o build/backtest.exe

C:\msys64\ucrt64\bin\g++ -std=c++17 -O2 -Icore ^
    core/mes_risk_main.cpp ^
    core/risk/RiskManager.cpp ^
    -o build/mes_risk.exe
```

Verify:
```
build\backtest.exe --help       # should print usage
build\mes_risk.exe              # should print usage
```

---

### 2. Download Historical Data

IB Gateway must be running on port 7497 (paper account).

```
py -3.11 scripts/download_history.py
```

Downloads ~6 months of MES 15-min RTH bars → `data/mes_15min.csv`

> **Note:** IB paper accounts cap historical data at ~6 months for 15-min bars regardless of duration requested. Live accounts provide up to 2 years.

---

### 3. Run Backtest

```
build\backtest.exe --csv data\mes_15min.csv
```

Optional parameter overrides (for testing only — do not tune on small samples):
```
build\backtest.exe --csv data\mes_15min.csv --sl-points 4 --rr 2 --ema-fast 5 --ema-slow 20 --adx-min 20
```

View monthly P&L breakdown:
```
py -3.11 scripts/monthly_pnl.py
```

Analyze data quality and trend regime:
```
py -3.11 scripts/analyze_data.py
```

Results are saved to `data/backtest_results.csv`. See [docs/backtest_results.md](docs/backtest_results.md) for the baseline run.

---

### 4. Paper Trading

Start IB Gateway → log in with paper credentials (username: `igzojp238`, port 7497).

```
py -3.11 main.py --mode paper
```

Dashboard: **http://localhost:8080**

The bot runs during RTH (09:45–15:30 ET). Signals fire on 15-min bar closes. Trades are logged to `data/trades_paper.json` and `data/scalping_performance_paper.csv`.

**Stop the bot:**
```
Ctrl+C
```

**If port 8080 is already in use** (previous run still running):
```powershell
Get-NetTCPConnection -LocalPort 8080 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

**Check logs:**
```
data\bot.log
```

---

### 5. Live Trading

Only after meeting all graduation criteria (see [Paper → Live Graduation Criteria](#paper--live-graduation-criteria)).

Switch IB Gateway to live account → port 7496.

```
py -3.11 main.py --mode live
```

Live mode uses 1 contract (vs 2 in paper). All other logic is identical.

> **Never run live and paper simultaneously** — they share client IDs and will conflict on the IB API.

---

## Strategy

Two signal types run concurrently. EMA is checked first every bar; ORB only fires if EMA is silent.

### Signal 1 — EMA Crossover
- **BUY** when EMA(5) crosses above EMA(20) AND price ≥ VWAP
- **Exit** when EMA(5) crosses below EMA(20), SL fills, TP fills, or EOD sweep
- Fires any time during RTH; no daily limit
- Stop: 4 pts fixed | TP: 8 pts | Contracts: up to 2 (paper)

### Signal 2 — ORB (Opening Range Breakout)
- Range built from first 2 RTH bars (9:30 and 9:45 ET); locked in at 10:00 ET
- **BUY** when bar closes above `orb_high`; only before noon ET
- Max 1 ORB entry per session (`orb_traded_today` flag)
- Stop: 1× Wilder's ATR (min 4 pts) | TP: 2× stop | Contracts: sized by ATR

**Signal priority**: EMA → ORB → HOLD. `not bot_state.in_trade` blocks double entries downstream.

### Signal Filters (all must pass before any entry)

| Filter | Condition | Reason |
|--------|-----------|--------|
| Market hours | 09:45–15:30 ET | Avoids open volatility and overnight gap risk |
| Bar quality | ≥ 90% of expected bars | Guards against stale indicators from data gaps |
| Warmup | 2 bars after overnight gap reset | Ensures indicators have converged |
| VWAP | Price ≥ VWAP (BUY only) | Trade with institutional flow, not against it |
| ATR spike | Current TR ≤ 2× Wilder ATR(14) | Skips volatility-explosion bars |
| ADX trend | ADX(14) ≥ 20 | EMA crossovers are noise in choppy markets |
| Volume | Bar volume ≥ 50% of 20-bar avg | Low volume = thin book = unreliable signals |
| SL cooldown | 30 min since last stop loss | Prevents re-entering the same whipsaw |
| ORB time gate | Signal before noon ET only | Morning momentum decays by midday |

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
│   ├── trades_paper.json                    full trade history (JSON)
│   ├── scalping_performance_paper.csv       per-trade CSV: signal_type, sl_points, pnl
│   ├── bot_state_paper.json                 daily/weekly P&L snapshot
│   └── regime_history_paper.json            ADX block rate history
└── docs/ib_gateway_setup.md
```

## Current Status (as of 2026-07-13)

**Mode**: Paper trading — building track record before live deployment.

**Trading record**:

| Date | Signal | Entry | Exit | Result |
|------|--------|-------|------|--------|
| 2026-07-01 | EMA BUY | 7553.75 | 7561.75 | TP +$76.60 |
| 2026-07-10 | ORB BUY | 7624.75 | — | EOD (unrecorded) |

**Open items before live**:
1. Accumulate ≥ 200 paper trades (currently 2)
2. Add ATR cap (~8 pts) so ORB entries don't return 0 contracts on high-volatility days
3. Add ORB short selling (`handle_sell`) for downside breakouts
4. Configure IB Gateway auto-restart at 3 AM CT (currently manual)

**Graduation criteria**: see [Paper → Live Graduation Criteria](#paper--live-graduation-criteria)

---

## Phase Roadmap

- **Phase 1** — Daily swing trading ([trading-bot](https://github.com/Baala/trading-bot), Alpaca)
- **Phase 2** — Enhanced signals + sentiment (trading-bot repo)
- **Phase 3** — THIS REPO — intraday scalping (IB TWS, MES futures)
- **Phase 4** — Live account, risk-scaled sizing ($5k+ capital)
