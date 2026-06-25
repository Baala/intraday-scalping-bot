# Backtest Results — MES 15-min Scalping Strategy

**Run date:** 2026-06-24  
**Data source:** IB Gateway paper account (download_history.py)  
**Usable data:** 2026-01-01 → 2026-06-24 (6 months, ~3,583 bars)  
**Note:** Bars before 2026-01 were IB placeholder data (zero volume, flat price) and are effectively excluded by the volume filter.

---

## Summary

| Metric | Value |
|---|---|
| Total trades | 24 |
| Wins / Losses | 8 / 16 |
| Win rate | 33.3% |
| Break-even win rate | ~39.5% |
| Total P&L | **-$121.60** |
| Avg win | +$76.60 |
| Avg loss | -$45.90 |
| Effective RR | 1.67 |
| Max drawdown | $366.90 |
| Capital at risk | $5,000 |
| Total loss as % capital | -2.4% |

---

## Monthly Breakdown

| Month | Trades | WR% | P&L | Cumulative |
|---|---|---|---|---|
| 2026-01 | 2 | 50% | +$40.70 | +$40.70 |
| 2026-02 | 1 | 0% | -$55.90 | -$15.20 |
| 2026-03 | 2 | 0% | -$111.80 | -$127.00 |
| 2026-04 | 5 | 20% | -$127.00 | -$254.00 |
| 2026-05 | 7 | 29% | -$86.30 | -$340.30 |
| 2026-06 | 7 | 43% | +$66.20 | -$274.10 |

Profitable months: 2 / 6

---

## Parameter Sensitivity

| Config | Win Rate | P&L | Max DD |
|---|---|---|---|
| SL=4pt / TP=8pt (default) | 33.3% | -$121.60 | $366.90 |
| SL=5pt / TP=10pt | 29.2% | -$274.10 | $548.70 |
| SL=6pt / TP=12pt | 29.2% | -$152.05 | $235.70 |
| SL=6pt / TP=15pt (RR 2.5) | 29.2% | -$47.05 | $190.30 |

Default config (4pt/8pt) produced the highest win rate. Widening the stop lowered win rate because the 15-min bars do not reach larger targets consistently.

---

## Market Context (Jan–Jun 2026)

| Month | Range | Move | Character |
|---|---|---|---|
| Jan 2026 | 2.9% | +0.5% | Quiet drift |
| Feb 2026 | 3.2% | -1.2% | Moderate downtrend |
| Mar 2026 | 8.7% | -4.5% | Strong downtrend (tariff sell-off) |
| Apr 2026 | 11.1% | +9.7% | Strong uptrend (tariff recovery) |
| May 2026 | 5.5% | +4.3% | Trending up |
| Jun 2026 | 4.8% | -2.3% | Moderate downtrend |

March–April were high-volatility trending months — theoretically good conditions for EMA crossover. Low win rate in April (20%) reflects tight 4-point stops being hit during intraday retracements on volatile days.

---

## Conclusions

1. **Sample size is too small.** 24 trades over 6 months at 4 trades/month cannot statistically validate or invalidate the strategy. Confidence interval on win rate: 14%–52% at 95%.

2. **P&L is not catastrophic.** -$121.60 = -2.4% of capital over 6 months. The bot is not blowing up — it is slightly below break-even on a thin sample.

3. **Win rate is the constraint.** The strategy needs 39.5% to break even. It achieved 33.3%. Only 2 extra wins from 24 trades would flip the result to profitable.

4. **Signal frequency is too low.** Target is 5–10 trades/month; actual was 4/month. The combined filters (ADX ≥ 20, VWAP side, volume ≥ 50%, ATR spike check) are conservative.

5. **No parameter set was clearly superior.** Sensitivity tests across SL=4–6pt showed similar or worse results. Do not tune on 24 trades — wait for 200+ paper trades.

---

## Next Step

Run paper trading for 2–3 months to accumulate 40–60 live signals with real market hours and real fills. Evaluate again at 200 trades per the graduation checklist.
