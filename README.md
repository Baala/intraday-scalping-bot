# Intraday Scalping Bot — MES Futures (Phase 3)

Real-time intraday scalping bot for MES (Micro S&P 500 Futures) via Interactive Brokers TWS API.

## Architecture

```
IB Gateway (port 7497)
    ↓  ib_insync WebSocket
Python ib_futures_stream.py
    ↓  accumulates 1-min bars
MA(5)/MA(20) crossover signal
    ↓  on BUY signal
C++ RiskManager (shared from trading-bot)
    ↓  asyncio subprocess call (non-blocking)
contracts=1, stop=entry-2pts, tp=entry+4pts
    ↓  ib.placeOrder()
IB Paper Account
```

## Risk

| Parameter | Value |
|-----------|-------|
| Capital | $1,000 |
| Risk/trade | $10 (1%) |
| Stop loss | 2 points ($10) |
| Take profit | 4 points ($20) |
| Max contracts | 1 |
| Commission | $0.85/side = $1.70 round trip |
| Net win (after commission) | $18.30 |
| Net loss (after commission) | $11.70 |
| Effective RR | 1.56 |
| Max daily loss (CB) | $30 (3% of capital) |

## Quick Start

1. Install IB Gateway — see [docs/ib_gateway_setup.md](docs/ib_gateway_setup.md)
2. `pip install ib_insync pytz`
3. Start IB Gateway on port 7497 (paper trading)
4. `python paper_trading/ib_futures_stream.py`

## Strategy

1-minute MA(5)/MA(20) crossover on MES:
- **BUY** when MA(5) crosses above MA(20)
- Stop: entry − 2 points ($10 risk)
- Target: entry + 4 points ($20 gross, $18.30 net)
- Only trade 09:30–15:45 ET (liquid hours)
- Unlimited trades/day — $30 daily loss circuit breaker stops trading

## MES Contract Specs

- Exchange: CME
- 1 index point = $5.00
- Tick size: 0.25 points = $1.25
- Margin: ~$500–600/contract (paper trading)
- PDT rule: does NOT apply (futures)

## Monthly P&L Estimates (paper trading)

| Scenario | Win Rate | Monthly |
|----------|----------|---------|
| Best (trending market) | 60% | +$440 |
| Realistic | 45% | +$80 |
| Break-even | 42% | $0 |
| Worst (CB fires daily) | 25% | −$300 max |

Estimates based on ~4 trades/day, 20 trading days, $1.70 commission/round trip.

## Stack

- Python 3.11+ / ib_insync 0.9+ / pytz
- C++ risk engine (g++ 11+)
- Interactive Brokers paper account (free)

## Phase Roadmap

- **Phase 1** — Daily swing trading ([trading-bot](https://github.com/Baala/trading-bot), Alpaca)
- **Phase 2** — Enhanced signals + sentiment (trading-bot repo)
- **Phase 3** — THIS REPO — intraday scalping (IB TWS, MES futures)
- **Phase 4** — Live account, risk-scaled sizing ($5k+ capital)
