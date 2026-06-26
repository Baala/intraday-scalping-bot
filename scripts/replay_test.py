"""
Replay test: feed data/mes_15min.csv through the full bot logic and verify behaviour.

Runs every bar through the same indicator math, signal detection, filters, and
trade simulation as the live bot — without needing IB Gateway or ib_insync.

Usage:
    py -3.11 scripts/replay_test.py
    py -3.11 scripts/replay_test.py --csv data/mes_15min.csv --verbose
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Optional

import pytz

# ── Config ───────────────────────────────────────────────────────────────────

with open("config/scalping_config.json") as _f:
    CFG = json.load(_f)

ET              = pytz.timezone("US/Eastern")
POINT_VALUE     = CFG["point_value"]
COMMISSION      = CFG["commission_per_side_usd"]
SL_PTS          = CFG["stop_loss_points"]
TP_PTS          = SL_PTS * CFG["reward_ratio"]
MAX_DAILY_LOSS  = CFG["capital_usd"] * 0.03
MAX_WEEKLY_LOSS = CFG["capital_usd"] * (CFG["max_weekly_loss_pct"] / 100.0)
ATR_PERIOD      = CFG["atr_period"]
ATR_SPIKE_MULT  = CFG["atr_spike_mult"]
ADX_PERIOD      = CFG["adx_period"]
ADX_MULT        = 2.0 / (ADX_PERIOD + 1)
EMA_FAST        = CFG["ema_fast"]
EMA_SLOW        = CFG["ema_slow"]
EMA_FAST_MULT   = 2.0 / (EMA_FAST + 1)
EMA_SLOW_MULT   = 2.0 / (EMA_SLOW + 1)
VWAP_ANCHOR     = time(9, 30)
MARKET_OPEN     = time(9, 45)
MARKET_CLOSE    = time(15, 30)
CONTRACTS       = CFG["max_contracts_paper"]
SL_COOLDOWN_MIN = CFG["sl_cooldown_minutes"]
VOL_FILTER_PCT  = CFG["volume_filter_pct"]
ADX_MIN         = CFG["adx_min"]
BAR_QUALITY_MIN = CFG["bar_quality_min_pct"]
CONSEC_SL_PAUSE = CFG["consecutive_sl_pause"]
WIN_RATE_PAUSE  = CFG["win_rate_pause_pct"] / 100.0
WIN_RATE_LOOKBACK = CFG["win_rate_lookback"]

# ── Bar ───────────────────────────────────────────────────────────────────────

@dataclass
class Bar:
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def load_csv(path: str) -> list[Bar]:
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            dt_naive = datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S")
            dt_et = ET.localize(dt_naive)
            bars.append(Bar(
                date=dt_et,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            ))
    return bars


# ── Indicators ────────────────────────────────────────────────────────────────

class Indicators:
    def __init__(self):
        self.ema_fast: Optional[float] = None
        self.ema_slow: Optional[float] = None
        self.prev_ema_fast: Optional[float] = None
        self.prev_ema_slow: Optional[float] = None
        self._warmup: deque = deque(maxlen=EMA_SLOW)

        self.cum_tpv: float = 0.0
        self.cum_vol: float = 0.0
        self.vwap_date: Optional[date] = None

        self.adx_ph: Optional[float] = None
        self.adx_pl: Optional[float] = None
        self.adx_pc: Optional[float] = None
        self.dm_plus_ema: Optional[float] = None
        self.dm_minus_ema: Optional[float] = None
        self.tr_ema: Optional[float] = None
        self.dx_ema: Optional[float] = None

        self.ohlcv: deque = deque(maxlen=ATR_PERIOD + 1)
        self.volumes: deque = deque(maxlen=20)

    def update_ema(self, close: float) -> None:
        self._warmup.append(close)
        self.prev_ema_fast, self.prev_ema_slow = self.ema_fast, self.ema_slow
        if self.ema_fast is None:
            if len(self._warmup) >= EMA_FAST:
                self.ema_fast = sum(list(self._warmup)[-EMA_FAST:]) / EMA_FAST
        else:
            self.ema_fast += EMA_FAST_MULT * (close - self.ema_fast)
        if self.ema_slow is None:
            if len(self._warmup) >= EMA_SLOW:
                self.ema_slow = sum(self._warmup) / EMA_SLOW
        else:
            self.ema_slow += EMA_SLOW_MULT * (close - self.ema_slow)

    def update_vwap(self, bar: Bar) -> Optional[float]:
        bar_et = bar.date.astimezone(ET)
        if bar_et.time() < VWAP_ANCHOR:
            return None
        if self.vwap_date != bar_et.date():
            self.cum_tpv = 0.0
            self.cum_vol = 0.0
            self.vwap_date = bar_et.date()
        tp = (bar.high + bar.low + bar.close) / 3.0
        self.cum_tpv += tp * bar.volume
        self.cum_vol += bar.volume
        return self.cum_tpv / self.cum_vol if self.cum_vol > 0 else None

    def update_adx(self, bar: Bar) -> Optional[float]:
        if self.adx_pc is None:
            self.adx_ph, self.adx_pl, self.adx_pc = bar.high, bar.low, bar.close
            return None
        up   = bar.high - self.adx_ph
        down = self.adx_pl - bar.low
        dm_p = up   if (up > down and up > 0)   else 0.0
        dm_m = down if (down > up and down > 0) else 0.0
        tr   = max(bar.high - bar.low,
                   abs(bar.high - self.adx_pc),
                   abs(bar.low  - self.adx_pc))
        self.adx_ph, self.adx_pl, self.adx_pc = bar.high, bar.low, bar.close

        def _ema(prev, val):
            return val if prev is None else prev + ADX_MULT * (val - prev)

        self.dm_plus_ema  = _ema(self.dm_plus_ema,  dm_p)
        self.dm_minus_ema = _ema(self.dm_minus_ema, dm_m)
        self.tr_ema       = _ema(self.tr_ema,       tr)
        if not self.tr_ema:
            return None
        di_p  = 100.0 * self.dm_plus_ema  / self.tr_ema
        di_m  = 100.0 * self.dm_minus_ema / self.tr_ema
        di_sum = di_p + di_m
        dx    = 100.0 * abs(di_p - di_m) / di_sum if di_sum > 0 else 0.0
        self.dx_ema = _ema(self.dx_ema, dx)
        return self.dx_ema

    def compute_atr(self) -> tuple[Optional[float], Optional[float]]:
        bars = list(self.ohlcv)
        if len(bars) < 2:
            return None, None
        trs = [max(b.high - b.low,
                   abs(b.high - bars[i-1].close),
                   abs(b.low  - bars[i-1].close))
               for i, b in enumerate(bars) if i > 0]
        if not trs:
            return None, None
        atr = sum(trs) / len(trs)
        current_tr = trs[-1]
        return atr, current_tr

    def signal(self) -> str:
        ef, es = self.ema_fast, self.ema_slow
        pf, ps = self.prev_ema_fast, self.prev_ema_slow
        if ef is None or es is None or pf is None or ps is None:
            return "HOLD"
        if pf <= ps and ef > es:
            return "BUY"
        if pf >= ps and ef < es:
            return "SELL"
        return "HOLD"

    def volume_ok(self, vol: float) -> bool:
        if len(self.volumes) < 5:
            return True
        avg = sum(self.volumes) / len(self.volumes)
        return avg == 0 or vol >= avg * (VOL_FILTER_PCT / 100.0)


# ── Trade simulator ───────────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_bar_et: str
    entry_price: float
    sl: float
    tp: float
    contracts: int
    exit_bar_et: str = ""
    exit_price: float = 0.0
    reason: str = ""
    pnl: float = 0.0


@dataclass
class SimState:
    in_trade: bool = False
    trade: Optional[Trade] = None
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    total_pnl: float = 0.0
    circuit_breaker: bool = False
    weekly_cb: bool = False
    paused: bool = False
    pause_reason: str = ""
    consecutive_sl: int = 0
    recent_results: deque = field(default_factory=lambda: deque(maxlen=WIN_RATE_LOOKBACK))
    sl_cooldown_until: Optional[datetime] = None
    trades: list = field(default_factory=list)
    last_trade_date: Optional[date] = None
    last_week: Optional[int] = None

    # Stats
    wins: int = 0
    losses: int = 0
    daily_stats: dict = field(default_factory=dict)
    filter_hits: dict = field(default_factory=lambda: {
        "adx": 0, "atr": 0, "volume": 0, "vwap": 0,
        "cooldown": 0, "cb": 0, "paused": 0, "quality": 0,
    })

    def net_pnl(self, pts: float) -> float:
        gross = pts * POINT_VALUE * self.trade.contracts
        comm  = COMMISSION * 2 * self.trade.contracts
        return gross - comm

    def reset_daily(self, d: date) -> None:
        self.daily_pnl = 0.0
        self.circuit_breaker = False
        self.last_trade_date = d

    def reset_weekly(self, week: int) -> None:
        self.weekly_pnl = 0.0
        self.weekly_cb = False
        self.last_week = week


# ── Main replay ──────────────────────────────────────────────────────────────

def run_replay(csv_path: str, verbose: bool) -> None:
    bars = load_csv(csv_path)
    ind  = Indicators()
    sim  = SimState()

    print(f"\n{'='*70}")
    print(f"  REPLAY TEST — {csv_path}")
    print(f"  Bars loaded: {len(bars)}  |  SL={SL_PTS}pt  TP={TP_PTS}pt  "
          f"EMA({EMA_FAST}/{EMA_SLOW})  ADX>={ADX_MIN}")
    print(f"{'='*70}\n")

    bars_received_today = 0
    session_start_bar_idx: Optional[int] = None

    for i, bar in enumerate(bars):
        bar_et = bar.date.astimezone(ET)
        bar_time = bar_et.time()
        bar_date = bar_et.date()
        bar_label = bar_et.strftime("%Y-%m-%d %H:%M")

        # Daily / weekly reset
        week_num = bar_et.isocalendar()[1]
        if sim.last_trade_date != bar_date:
            bars_received_today = 0
            session_start_bar_idx = i
            sim.reset_daily(bar_date)
        if sim.last_week != week_num:
            sim.reset_weekly(week_num)

        # Only process RTH bars
        if not (MARKET_OPEN <= bar_time <= MARKET_CLOSE):
            continue

        # Track bars received for quality
        bars_received_today += 1
        if session_start_bar_idx is None:
            session_start_bar_idx = i
        elapsed_15min = max(1, (bar.date - bars[session_start_bar_idx].date).total_seconds() / 900 + 1)
        bar_quality = min(100.0, (bars_received_today / elapsed_15min) * 100.0)

        # EOD sweep at 15:30
        if bar_time >= MARKET_CLOSE and sim.in_trade:
            exit_price = bar.close
            pnl = sim.net_pnl(exit_price - sim.trade.entry_price)
            sim.trade.exit_bar_et = bar_label
            sim.trade.exit_price  = exit_price
            sim.trade.reason      = "EOD"
            sim.trade.pnl         = pnl
            sim.trades.append(sim.trade)
            sim.daily_pnl  += pnl
            sim.weekly_pnl += pnl
            sim.total_pnl  += pnl
            sim.in_trade = False
            result = "WIN" if pnl > 0 else "LOSS"
            sim.recent_results.append(result == "WIN")
            if pnl > 0:
                sim.wins += 1
            else:
                sim.losses += 1
            sim.consecutive_sl = 0
            print(f"  {bar_label}  EOD SWEEP ->exit={exit_price:.2f}  "
                  f"P&L={pnl:+.2f}  [{result}]  total={sim.total_pnl:+.2f}")

        # Feed indicators
        ind.update_ema(bar.close)
        ind.ohlcv.append(bar)
        ind.volumes.append(bar.volume)
        adx         = ind.update_adx(bar)
        vwap        = ind.update_vwap(bar)
        atr, cur_tr = ind.compute_atr()

        # Check open trade SL/TP
        if sim.in_trade and sim.trade:
            t = sim.trade
            sl_hit = bar.low  <= t.sl
            tp_hit = bar.high >= t.tp
            exit_reason = None
            if sl_hit and tp_hit:
                exit_reason, exit_price = "SL", t.sl
            elif sl_hit:
                exit_reason, exit_price = "SL", t.sl
            elif tp_hit:
                exit_reason, exit_price = "TP", t.tp

            if exit_reason:
                pnl = sim.net_pnl(exit_price - t.entry_price)
                t.exit_bar_et = bar_label
                t.exit_price  = exit_price
                t.reason      = exit_reason
                t.pnl         = pnl
                sim.trades.append(t)
                sim.daily_pnl  += pnl
                sim.weekly_pnl += pnl
                sim.total_pnl  += pnl
                sim.in_trade = False
                result = "WIN" if exit_reason == "TP" else "LOSS"
                sim.recent_results.append(result == "WIN")
                if result == "WIN":
                    sim.wins += 1
                    sim.consecutive_sl = 0
                else:
                    sim.losses += 1
                    sim.consecutive_sl += 1
                    sim.sl_cooldown_until = bar.date + timedelta(minutes=SL_COOLDOWN_MIN)

                # Update daily CB
                if sim.daily_pnl <= -MAX_DAILY_LOSS:
                    sim.circuit_breaker = True
                if sim.weekly_pnl <= -MAX_WEEKLY_LOSS:
                    sim.weekly_cb = True

                # Consecutive SL pause
                if sim.consecutive_sl >= CONSEC_SL_PAUSE and not sim.paused:
                    sim.paused = True
                    sim.pause_reason = f"{sim.consecutive_sl} consecutive SLs"

                # Rolling win-rate pause
                if len(sim.recent_results) >= WIN_RATE_LOOKBACK:
                    wr = sum(sim.recent_results) / len(sim.recent_results)
                    if wr < WIN_RATE_PAUSE and not sim.paused:
                        sim.paused = True
                        sim.pause_reason = f"win rate {wr:.0%} < {WIN_RATE_PAUSE:.0%}"

                print(f"  {bar_label}  {exit_reason} ->exit={exit_price:.2f}  "
                      f"P&L={pnl:+.2f}  [{result}]  "
                      f"consec_sl={sim.consecutive_sl}  total={sim.total_pnl:+.2f}")
                continue

        # Check EMA cross-down exit
        if sim.in_trade:
            sig = ind.signal()
            if sig == "SELL":
                exit_price = bar.close
                pnl = sim.net_pnl(exit_price - sim.trade.entry_price)
                sim.trade.exit_bar_et = bar_label
                sim.trade.exit_price  = exit_price
                sim.trade.reason      = "EMA_CROSS"
                sim.trade.pnl         = pnl
                sim.trades.append(sim.trade)
                sim.daily_pnl  += pnl
                sim.weekly_pnl += pnl
                sim.total_pnl  += pnl
                sim.in_trade = False
                result = "WIN" if pnl > 0 else "LOSS"
                sim.recent_results.append(result == "WIN")
                if pnl > 0:
                    sim.wins += 1
                else:
                    sim.losses += 1
                print(f"  {bar_label}  EMA CROSS EXIT ->exit={exit_price:.2f}  "
                      f"P&L={pnl:+.2f}  [{result}]  total={sim.total_pnl:+.2f}")
            continue

        # Signal detection
        sig  = ind.signal()
        vwap_blocked = (vwap is not None and bar.close < vwap)
        if sig == "BUY" and vwap_blocked:
            sig = "HOLD"

        # Verbose bar log
        if verbose and ind.ema_fast and ind.ema_slow and adx and vwap and atr:
            vol_avg   = sum(ind.volumes) / len(ind.volumes) if ind.volumes else 0
            vol_ratio = (bar.volume / vol_avg * 100) if vol_avg > 0 else 0
            print(f"  {bar_label}  {bar.close:.2f}  "
                  f"EMA({ind.ema_fast:.1f}/{ind.ema_slow:.1f})  "
                  f"sig={sig}  ADX={adx:.1f}  "
                  f"VWAP={vwap:.2f}  vol={vol_ratio:.0f}%  "
                  f"TR={cur_tr:.2f}/ATR={atr:.2f}")

        if sig != "BUY" or sim.in_trade:
            continue

        # Filter gate — log every blocked BUY
        def blocked(reason: str) -> bool:
            sim.filter_hits[reason.split("_")[0].lower()] = \
                sim.filter_hits.get(reason.split("_")[0].lower(), 0) + 1
            print(f"  {bar_label}  BUY BLOCKED: {reason}")
            return True

        if bar_quality < BAR_QUALITY_MIN:
            sim.filter_hits["quality"] += 1
            print(f"  {bar_label}  BUY BLOCKED: bar quality {bar_quality:.0f}% < {BAR_QUALITY_MIN}%")
            continue
        if sim.circuit_breaker:
            sim.filter_hits["cb"] += 1
            print(f"  {bar_label}  BUY BLOCKED: daily circuit breaker (daily P&L={sim.daily_pnl:.2f})")
            continue
        if sim.weekly_cb:
            sim.filter_hits["cb"] += 1
            print(f"  {bar_label}  BUY BLOCKED: weekly circuit breaker (weekly P&L={sim.weekly_pnl:.2f})")
            continue
        if sim.paused:
            sim.filter_hits["paused"] += 1
            print(f"  {bar_label}  BUY BLOCKED: paused ({sim.pause_reason})")
            continue
        if adx is not None and adx < ADX_MIN:
            sim.filter_hits["adx"] += 1
            print(f"  {bar_label}  BUY BLOCKED: ADX={adx:.1f} < {ADX_MIN}")
            continue
        if atr is not None and cur_tr is not None and cur_tr > ATR_SPIKE_MULT * atr:
            sim.filter_hits["atr"] += 1
            print(f"  {bar_label}  BUY BLOCKED: ATR spike TR={cur_tr:.2f} > {ATR_SPIKE_MULT}xATR={atr:.2f}")
            continue
        if not ind.volume_ok(bar.volume):
            vol_avg = sum(ind.volumes) / len(ind.volumes)
            sim.filter_hits["volume"] += 1
            print(f"  {bar_label}  BUY BLOCKED: volume {bar.volume:.0f} < {VOL_FILTER_PCT}% of avg {vol_avg:.0f}")
            continue
        if sim.sl_cooldown_until and bar.date < sim.sl_cooldown_until:
            sim.filter_hits["cooldown"] += 1
            print(f"  {bar_label}  BUY BLOCKED: SL cooldown until {sim.sl_cooldown_until.strftime('%H:%M')}")
            continue

        # Enter trade
        entry  = bar.close
        sl     = entry - SL_PTS
        tp     = entry + TP_PTS
        sim.trade    = Trade(bar_label, entry, sl, tp, CONTRACTS)
        sim.in_trade = True
        print(f"\n  {bar_label}  *** BUY ENTRY ***  "
              f"price={entry:.2f}  SL={sl:.2f}  TP={tp:.2f}  "
              f"contracts={CONTRACTS}  ADX={adx:.1f}  VWAP={vwap:.2f}")

    # ── Summary ──────────────────────────────────────────────────────────────

    total = sim.wins + sim.losses
    wr    = sim.wins / total * 100 if total else 0

    print(f"\n{'='*70}")
    print(f"  RESULTS")
    print(f"{'='*70}")
    print(f"  Total trades   : {total}  ({sim.wins}W / {sim.losses}L)")
    print(f"  Win rate       : {wr:.1f}%")
    print(f"  Total P&L      : ${sim.total_pnl:+.2f}")
    print(f"  Avg per trade  : ${sim.total_pnl/total:+.2f}" if total else "")

    print(f"\n  Filter hits:")
    for k, v in sorted(sim.filter_hits.items(), key=lambda x: -x[1]):
        if v:
            print(f"    {k:<12} {v}")

    print(f"\n  Trade log:")
    for t in sim.trades:
        print(f"    {t.entry_bar_et}  entry={t.entry_price:.2f}  "
              f"exit={t.exit_price:.2f} [{t.reason}]  P&L={t.pnl:+.2f}")

    # Monthly breakdown
    monthly: dict[str, float] = {}
    monthly_counts: dict[str, int] = {}
    for t in sim.trades:
        m = t.entry_bar_et[:7]
        monthly[m]        = monthly.get(m, 0.0) + t.pnl
        monthly_counts[m] = monthly_counts.get(m, 0) + 1

    print(f"\n  Monthly P&L:")
    for m in sorted(monthly):
        print(f"    {m}  {monthly_counts[m]:2d} trades  ${monthly[m]:+.2f}")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Replay test for MES scalping bot")
    p.add_argument("--csv",     default="data/mes_15min.csv", help="CSV path")
    p.add_argument("--verbose", action="store_true", help="Print every bar")
    args = p.parse_args()

    if not Path(args.csv).exists():
        print(f"ERROR: CSV not found: {args.csv}")
        print("Run: py -3.11 scripts/download_history.py")
        sys.exit(1)

    run_replay(args.csv, args.verbose)
