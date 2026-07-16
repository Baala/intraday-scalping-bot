# ORB — Opening Range Breakout

## What it is

ORB trades the first significant price move after the market "decides" a direction. The opening 15–30 minutes are a tug-of-war between buyers and sellers. When price finally breaks out of that range, the winning side tends to carry momentum.

---

## How the bot uses it (step by step)

### Step 1 — Build the range (09:45–10:00 ET)

The bot watches the first two 15-min RTH bars and records:

```
orb_high = highest price across those bars
orb_low  = lowest price across those bars
```

### Step 2 — Wait for breakout (10:00–12:00 ET)

On each 15-min bar close, the bot checks:

```
close > orb_high  →  ORB LONG  (breakout upward)
close < orb_low   →  ORB SHORT (breakout downward)
```

After 12:00 ET, no new ORB signals fire (too late in the session).

### Step 3 — EMA direction filter

Before entering, confirm the trend agrees:

```
ORB LONG  only if  EMA5 > EMA20  (uptrend)
ORB SHORT only if  EMA5 < EMA20  (downtrend)
```

If the trend disagrees, the signal is blocked and logged as `ema_trend` filter hit.

### Step 4 — Enter with ATR-sized bracket

```
SL  = entry − ATR        (capped at 8 pts)
TP  = entry + ATR × 2   (capped at 16 pts)
```

The ATR cap limits the maximum loss per trade even on very volatile days.

### Step 5 — One trade per day

`orb_traded_today` is set to True the moment a signal fires. No second ORB signal is taken that day regardless of outcome.

---

## Real example — 2026-07-14

```
Opening range built 09:45–10:00:
  orb_high ≈ 7588

10:15 bar closes at 7594 → above 7588 → ORB LONG
  ATR at entry = 13.62  →  capped to 8 pts
  Entry = 7596.75
  SL    = 7588.75  (entry − 8)
  TP    = 7612.75  (entry + 16)

Price dropped to 7574 → hit SL → −$76
```

---

## Key parameters (scalping_config.json)

| Parameter | Value | Meaning |
|---|---|---|
| `orb_atr_cap_pts` | 8.0 | Max SL size in points |
| `reward_ratio` | 2.0 | TP = 2 × SL |
| `bar_quality_min_pct` | 70 | Skip if <70% of bars arrived on time |
| `adx_min` | 20 | Skip if market is too choppy |
| `volume_filter_pct` | 50 | Skip if volume < 50% of recent avg |
| `sl_cooldown_minutes` | 30 | Wait 30 min after an SL before re-entering |

---

## Why it works

- Breakouts from tight ranges have a defined risk (SL just outside the range)
- EMA filter removes counter-trend false breakouts
- 1-trade-per-day limit prevents revenge trading after a loss
- ATR cap keeps loss size predictable on high-volatility days

---

## ATR — Average True Range

ATR measures how much the market typically moves per bar. Think of it as the market's "normal wiggle size."

**How it's calculated (per bar):**
```
True Range = biggest of:
  1. bar high − bar low           (range of this bar)
  2. |bar high − previous close|  (gap up)
  3. |bar low  − previous close|  (gap down)

ATR = average of True Range over last 14 bars
```

**What it tells you:**
```
Quiet day     → ATR =  4 pts → bars typically move $20/contract
Volatile day  → ATR = 13 pts → bars typically swing $65/contract
```

**How the bot uses ATR:**

| Use | Logic |
|---|---|
| SL sizing | SL = entry − ATR, capped at 8pts to limit max loss |
| Spike filter | If this bar's range > 2 × ATR, skip the trade (market too wild) |

**Today's example (2026-07-14):**
```
ATR = 13.62 pts  →  market was moving ~$68 per bar
SL cap = 8 pts   →  bot capped loss at $40/contract

Without cap: SL at 7583.13 (still hit at 7574), loss = $68
With cap:    SL at 7588.75 (still hit at 7574), loss = $40
```

---

## Why it sometimes fails

- The breakout bar itself can be a momentum spike — entry is at the close of that bar, which may be near its high. A pullback on the next bar can hit the SL before the trend resumes.
- In choppy markets (no clear trend, EMA5 ≈ EMA20), the filter may not block marginal signals.
- 15-min bars mean SL/TP checks only happen every 15 minutes — intrabar moves are caught by the software guard, but the exit price will be the SL/TP level, not the actual bar low/high.
