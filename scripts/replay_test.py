"""
Replay test: feed data/mes_15min.csv through the full bot logic and verify behaviour.

Runs every bar through the same indicator math, signal detection, filters, and
trade simulation as the live bot — without needing IB Gateway or ib_insync.

Simulates both signal layers:
  - EMA(5/20) crossover  → LONG or SHORT entry
  - ORB breakout         → LONG (above range high) or SHORT (below range low)

Usage:
    py -3.11 scripts/replay_test.py
    py -3.11 scripts/replay_test.py --csv data/mes_15min.csv --verbose
    py -3.11 scripts/replay_test.py --quality 70
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

# Capital is overridable via --capital flag; set after arg parse
_CAPITAL_DEFAULT = CFG["capital_usd"]
MAX_DAILY_LOSS  = _CAPITAL_DEFAULT * 0.03
MAX_WEEKLY_LOSS = _CAPITAL_DEFAULT * (CFG["max_weekly_loss_pct"] / 100.0)
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
LAST_ENTRY      = time(15, 0)
ORB_CUTOFF      = time(10, 30)  # 3-bar ORB: 09:45 + 10:00 + 10:15 bars; signals from 10:30 ET
ORB_SIG_CUTOFF  = time(12, 0)
ORB_ATR_CAP     = CFG["orb_atr_cap_pts"]
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

    def ema_signal(self) -> str:
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
    direction: str = "LONG"
    signal_type: str = "EMA"
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

    # ORB state (reset daily)
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_traded_today: bool = False

    # Stats
    wins: int = 0
    losses: int = 0
    filter_hits: dict = field(default_factory=lambda: {
        "adx": 0, "atr": 0, "volume": 0, "vwap": 0,
        "cooldown": 0, "cb": 0, "paused": 0, "quality": 0, "gate": 0,
        "ema_trend": 0,
    })

    def net_pnl(self, exit_price: float) -> float:
        d = 1.0 if self.trade.direction == "LONG" else -1.0
        gross = d * (exit_price - self.trade.entry_price) * POINT_VALUE * self.trade.contracts
        return gross - COMMISSION * 2 * self.trade.contracts

    def reset_daily(self, d: date) -> None:
        self.daily_pnl = 0.0
        self.circuit_breaker = False
        self.last_trade_date = d
        self.orb_high = None
        self.orb_low = None
        self.orb_traded_today = False

    def reset_weekly(self, week: int) -> None:
        self.weekly_pnl = 0.0
        self.weekly_cb = False
        self.last_week = week

    def record_exit(self, exit_price: float, reason: str, bar_label: str,
                    no_pause: bool = False) -> float:
        t = self.trade
        pnl = self.net_pnl(exit_price)
        t.exit_bar_et = bar_label
        t.exit_price  = exit_price
        t.reason      = reason
        t.pnl         = pnl
        self.trades.append(t)
        self.daily_pnl  += pnl
        self.weekly_pnl += pnl
        self.total_pnl  += pnl
        self.in_trade   = False
        self.trade      = None
        is_win = pnl > 0
        self.recent_results.append(is_win)
        if is_win:
            self.wins += 1
            self.consecutive_sl = 0
        else:
            self.losses += 1
            if reason == "SL":
                self.consecutive_sl += 1
                self.sl_cooldown_until = None
        if self.daily_pnl <= -MAX_DAILY_LOSS:
            self.circuit_breaker = True
        if self.weekly_pnl <= -MAX_WEEKLY_LOSS:
            self.weekly_cb = True
        if not no_pause:
            if self.consecutive_sl >= CONSEC_SL_PAUSE and not self.paused:
                self.paused = True
                self.pause_reason = f"{self.consecutive_sl} consecutive SLs"
            if len(self.recent_results) >= WIN_RATE_LOOKBACK:
                wr = sum(self.recent_results) / len(self.recent_results)
                if wr < WIN_RATE_PAUSE and not self.paused:
                    self.paused = True
                    self.pause_reason = f"win rate {wr:.0%} < {WIN_RATE_PAUSE:.0%}"
        return pnl


# ── Main replay ──────────────────────────────────────────────────────────────

def run_replay(csv_path: str, verbose: bool, quality_min: float = BAR_QUALITY_MIN,
               no_pause: bool = False, no_ema: bool = False,
               ema_filter: bool = False, atr_cap: float = ORB_ATR_CAP,
               capital: float = _CAPITAL_DEFAULT, orb_bars: int = 2,
               ema_short_only: bool = False) -> None:
    global MAX_DAILY_LOSS, MAX_WEEKLY_LOSS
    MAX_DAILY_LOSS  = capital * 0.03
    MAX_WEEKLY_LOSS = capital * (CFG["max_weekly_loss_pct"] / 100.0)

    from datetime import timedelta
    _orb_cutoff = (datetime.combine(date.today(), MARKET_OPEN)
                   + timedelta(minutes=15 * orb_bars)).time()

    bars = load_csv(csv_path)
    ind  = Indicators()
    sim  = SimState()

    pause_label  = "  NO-PAUSE (signal quality mode)" if no_pause else ""
    filter_label = "  EMA-TREND-FILTER" if ema_filter else ""
    sig_label    = "ORB only" if no_ema else "EMA LONG/SHORT + ORB LONG/SHORT"
    print(f"\n{'='*70}")
    print(f"  REPLAY TEST — {csv_path}")
    print(f"  Bars: {len(bars)}  SL={SL_PTS}pt  TP={TP_PTS}pt  "
          f"EMA({EMA_FAST}/{EMA_SLOW})  ADX>={ADX_MIN}  quality>={quality_min:.0f}%  "
          f"ORB_ATR_CAP={atr_cap}pt  capital=${capital:,.0f}  ORB_BARS={orb_bars}{pause_label}{filter_label}")
    print(f"  Signals: {sig_label}  |  range locked after {_orb_cutoff.strftime('%H:%M')} ET  |  last_entry=15:00 ET")
    print(f"{'='*70}\n")

    bars_received_today = 0
    session_start_bar_idx: Optional[int] = None

    for i, bar in enumerate(bars):
        bar_et    = bar.date.astimezone(ET)
        bar_time  = bar_et.time()
        bar_date  = bar_et.date()
        bar_label = bar_et.strftime("%Y-%m-%d %H:%M")

        # ── Daily / weekly reset ──────────────────────────────────────────────
        week_num = bar_et.isocalendar()[1]
        if sim.last_trade_date != bar_date:
            bars_received_today = 0
            session_start_bar_idx = i
            sim.reset_daily(bar_date)
        if sim.last_week != week_num:
            sim.reset_weekly(week_num)

        # ── ORB range: built from RTH bars before the cutoff ─────────────────
        if MARKET_OPEN <= bar_time < _orb_cutoff:
            sim.orb_high = max(bar.high, sim.orb_high) if sim.orb_high else bar.high
            sim.orb_low  = min(bar.low,  sim.orb_low)  if sim.orb_low  else bar.low

        # ── Skip non-RTH bars ─────────────────────────────────────────────────
        if not (MARKET_OPEN <= bar_time <= MARKET_CLOSE):
            continue

        # ── Bar quality ───────────────────────────────────────────────────────
        bars_received_today += 1
        if session_start_bar_idx is None:
            session_start_bar_idx = i
        elapsed_slots = max(1, (bar.date - bars[session_start_bar_idx].date).total_seconds() / 900 + 1)
        bar_quality = min(100.0, (bars_received_today / elapsed_slots) * 100.0)

        # ── EOD sweep at 15:30 ────────────────────────────────────────────────
        if bar_time >= MARKET_CLOSE and sim.in_trade:
            pnl = sim.record_exit(bar.close, "EOD", bar_label, no_pause=no_pause)
            result = "WIN" if pnl > 0 else "LOSS"
            print(f"  {bar_label}  [{sim.trade if sim.trade else 'EOD'}] "
                  f"EOD SWEEP  exit={bar.close:.2f}  P&L={pnl:+.2f}  "
                  f"[{result}]  total={sim.total_pnl:+.2f}")

        # ── Feed indicators ───────────────────────────────────────────────────
        ind.update_ema(bar.close)
        ind.ohlcv.append(bar)
        ind.volumes.append(bar.volume)
        adx         = ind.update_adx(bar)
        vwap        = ind.update_vwap(bar)
        atr, cur_tr = ind.compute_atr()

        # ── Check open trade SL/TP (direction-aware) ──────────────────────────
        if sim.in_trade and sim.trade:
            t = sim.trade
            if t.direction == "LONG":
                sl_hit = bar.low  <= t.sl
                tp_hit = bar.high >= t.tp
            else:
                sl_hit = bar.high >= t.sl
                tp_hit = bar.low  <= t.tp

            exit_reason = None
            if sl_hit and tp_hit:
                exit_reason, exit_price = "SL", t.sl
            elif sl_hit:
                exit_reason, exit_price = "SL", t.sl
            elif tp_hit:
                exit_reason, exit_price = "TP", t.tp

            if exit_reason:
                pnl = sim.record_exit(exit_price, exit_reason, bar_label, no_pause=no_pause)
                if exit_reason == "SL" and sim.sl_cooldown_until is None:
                    sim.sl_cooldown_until = bar.date + timedelta(minutes=SL_COOLDOWN_MIN)
                result = "WIN" if pnl > 0 else "LOSS"
                print(f"  {bar_label}  {t.signal_type} {t.direction} "
                      f"{exit_reason}  exit={exit_price:.2f}  P&L={pnl:+.2f}  "
                      f"[{result}]  consec_sl={sim.consecutive_sl}  "
                      f"total={sim.total_pnl:+.2f}")
                continue

        # ── EMA cross-direction exit for open position ─────────────────────────
        if sim.in_trade and sim.trade:
            ema_sig = ind.ema_signal()
            # Close LONG on bearish crossover; close SHORT on bullish crossover
            cross_exit = (sim.trade.direction == "LONG"  and ema_sig == "SELL") or \
                         (sim.trade.direction == "SHORT" and ema_sig == "BUY")
            if cross_exit:
                pnl = sim.record_exit(bar.close, "EMA_CROSS", bar_label, no_pause=no_pause)
                result = "WIN" if pnl > 0 else "LOSS"
                print(f"  {bar_label}  EMA CROSS EXIT ({sim.trade.direction if sim.trade else ''})  "
                      f"exit={bar.close:.2f}  P&L={pnl:+.2f}  [{result}]  "
                      f"total={sim.total_pnl:+.2f}")
            continue

        # ── Signal detection ──────────────────────────────────────────────────
        if sim.in_trade:
            continue

        ema_sig = ind.ema_signal()
        sig        = "HOLD"
        sig_type   = ""
        direction  = "LONG"
        sl_pts_use = SL_PTS
        tp_pts_use = TP_PTS

        # Last-entry gate
        if bar_time >= LAST_ENTRY:
            if ema_sig in ("BUY", "SELL"):
                sim.filter_hits["gate"] = sim.filter_hits.get("gate", 0) + 1
                if verbose:
                    print(f"  {bar_label}  {ema_sig} BLOCKED: past 15:00 ET gate")
            # ORB also blocked
        else:
            # EMA signal
            if not no_ema:
                vwap_ok_long  = vwap is None or bar.close >= vwap
                vwap_ok_short = vwap is None or bar.close <= vwap
                if ema_sig == "BUY" and vwap_ok_long:
                    sig, sig_type, direction = "ENTRY", "EMA", "LONG"
                elif ema_sig == "SELL" and vwap_ok_short:
                    sig, sig_type, direction = "ENTRY", "EMA", "SHORT"
                elif ema_sig == "BUY" and not vwap_ok_long:
                    sim.filter_hits["vwap"] += 1
                    if verbose:
                        print(f"  {bar_label}  BUY BLOCKED: close {bar.close:.2f} < VWAP {vwap:.2f}")
                elif ema_sig == "SELL" and not vwap_ok_short:
                    sim.filter_hits["vwap"] += 1
                    if verbose:
                        print(f"  {bar_label}  SELL BLOCKED: close {bar.close:.2f} > VWAP {vwap:.2f}")

            # ORB signal (overrides EMA if ORB fires and ORB not yet traded)
            if (sig == "HOLD" and sim.orb_high is not None and
                    not sim.orb_traded_today and
                    bar_time >= _orb_cutoff and bar_time < ORB_SIG_CUTOFF):
                if bar.close > sim.orb_high:
                    if ema_filter and ind.ema_fast and ind.ema_slow and ind.ema_fast <= ind.ema_slow:
                        sim.filter_hits["ema_trend"] += 1
                        if verbose:
                            print(f"  {bar_label}  ORB LONG BLOCKED: EMA trend bearish ({ind.ema_fast:.2f}<={ind.ema_slow:.2f})")
                    else:
                        sig, sig_type, direction = "ENTRY", "ORB", "LONG"
                elif bar.close < sim.orb_low:
                    if (ema_filter or ema_short_only) and ind.ema_fast and ind.ema_slow and ind.ema_fast >= ind.ema_slow:
                        sim.filter_hits["ema_trend"] += 1
                        if verbose:
                            print(f"  {bar_label}  ORB SHORT BLOCKED: EMA trend bullish ({ind.ema_fast:.2f}>={ind.ema_slow:.2f})")
                    else:
                        sig, sig_type, direction = "ENTRY", "ORB", "SHORT"
                if sig == "ENTRY":
                    # ATR-capped SL for ORB
                    if atr:
                        sl_pts_use = max(min(atr, atr_cap), SL_PTS)
                    tp_pts_use = sl_pts_use * CFG["reward_ratio"]

        if sig != "ENTRY":
            if verbose and ind.ema_fast and ind.ema_slow and adx and vwap and atr:
                vol_avg   = sum(ind.volumes) / len(ind.volumes) if ind.volumes else 0
                vol_ratio = (bar.volume / vol_avg * 100) if vol_avg else 0
                print(f"  {bar_label}  {bar.close:.2f}  "
                      f"EMA({ind.ema_fast:.1f}/{ind.ema_slow:.1f})  "
                      f"sig={ema_sig}  ADX={adx:.1f}  VWAP={vwap:.2f}  "
                      f"vol={vol_ratio:.0f}%")
            continue

        # ── Filter gate ───────────────────────────────────────────────────────
        def blocked(reason: str, detail: str = "") -> bool:
            key = reason.split("_")[0].lower()
            sim.filter_hits[key] = sim.filter_hits.get(key, 0) + 1
            print(f"  {bar_label}  {sig_type} {direction} BLOCKED: {reason} {detail}".rstrip())
            return True

        if bar_quality < quality_min:
            if blocked("quality", f"{bar_quality:.0f}% < {quality_min:.0f}%"): continue
        if sim.circuit_breaker:
            if blocked("cb", f"daily CB (daily={sim.daily_pnl:.2f})"): continue
        if sim.weekly_cb:
            if blocked("cb", f"weekly CB (weekly={sim.weekly_pnl:.2f})"): continue
        if sim.paused:
            if blocked("paused", f"({sim.pause_reason})"): continue
        if adx is not None and adx < ADX_MIN:
            if blocked("adx", f"ADX={adx:.1f} < {ADX_MIN}"): continue
        if atr is not None and cur_tr is not None and cur_tr > ATR_SPIKE_MULT * atr:
            if blocked("atr", f"spike TR={cur_tr:.2f} > {ATR_SPIKE_MULT}×ATR={atr:.2f}"): continue
        if not ind.volume_ok(bar.volume):
            vol_avg = sum(ind.volumes) / len(ind.volumes)
            if blocked("volume", f"{bar.volume:.0f} < {VOL_FILTER_PCT}% of avg {vol_avg:.0f}"): continue
        if sim.sl_cooldown_until and bar.date < sim.sl_cooldown_until:
            if blocked("cooldown", f"until {sim.sl_cooldown_until.strftime('%H:%M')}"): continue

        # ── Enter trade ───────────────────────────────────────────────────────
        entry = bar.close
        if direction == "LONG":
            sl = entry - sl_pts_use
            tp = entry + tp_pts_use
        else:
            sl = entry + sl_pts_use
            tp = entry - tp_pts_use

        sim.trade    = Trade(bar_label, entry, sl, tp, CONTRACTS,
                             direction=direction, signal_type=sig_type)
        sim.in_trade = True
        if sig_type == "ORB":
            sim.orb_traded_today = True

        vwap_str = f"{vwap:.2f}" if vwap else "n/a"
        print(f"\n  {bar_label}  *** {sig_type} {direction} ENTRY ***  "
              f"price={entry:.2f}  SL={sl:.2f}  TP={tp:.2f}  "
              f"contracts={CONTRACTS}  ADX={adx:.1f}  VWAP={vwap_str}")

    # ── Summary ───────────────────────────────────────────────────────────────
    total = sim.wins + sim.losses
    wr    = sim.wins / total * 100 if total else 0

    longs  = [t for t in sim.trades if t.direction == "LONG"]
    shorts = [t for t in sim.trades if t.direction == "SHORT"]
    orb_t  = [t for t in sim.trades if t.signal_type == "ORB"]
    ema_t  = [t for t in sim.trades if t.signal_type == "EMA"]

    print(f"\n{'='*70}")
    print(f"  RESULTS  (quality>={quality_min:.0f}%)")
    print(f"{'='*70}")
    print(f"  Total trades : {total}  ({sim.wins}W / {sim.losses}L)  "
          f"win rate={wr:.1f}%")
    print(f"  Total P&L    : ${sim.total_pnl:+.2f}")
    print(f"  Avg/trade    : ${sim.total_pnl/total:+.2f}" if total else "  Avg/trade: n/a")
    print(f"\n  By signal:")
    print(f"    EMA  {len(ema_t):3d} trades  "
          f"${sum(t.pnl for t in ema_t):+.2f}  "
          f"WR={sum(t.pnl>0 for t in ema_t)/len(ema_t)*100:.0f}%" if ema_t else "    EMA    0 trades")
    print(f"    ORB  {len(orb_t):3d} trades  "
          f"${sum(t.pnl for t in orb_t):+.2f}  "
          f"WR={sum(t.pnl>0 for t in orb_t)/len(orb_t)*100:.0f}%" if orb_t else "    ORB    0 trades")
    print(f"\n  By direction:")
    print(f"    LONG  {len(longs):3d} trades  "
          f"${sum(t.pnl for t in longs):+.2f}  "
          f"WR={sum(t.pnl>0 for t in longs)/len(longs)*100:.0f}%" if longs else "    LONG   0 trades")
    print(f"    SHORT {len(shorts):3d} trades  "
          f"${sum(t.pnl for t in shorts):+.2f}  "
          f"WR={sum(t.pnl>0 for t in shorts)/len(shorts)*100:.0f}%" if shorts else "    SHORT  0 trades")

    print(f"\n  Filter hits:")
    for k, v in sorted(sim.filter_hits.items(), key=lambda x: -x[1]):
        if v:
            print(f"    {k:<12} {v}")

    print(f"\n  Trade log:")
    for t in sim.trades:
        print(f"    {t.entry_bar_et}  {t.signal_type} {t.direction}  "
              f"entry={t.entry_price:.2f}  exit={t.exit_price:.2f} "
              f"[{t.reason}]  P&L={t.pnl:+.2f}")

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
    p.add_argument("--quality",   type=float, default=None,
                   help="Override bar_quality_min_pct (e.g. 70)")
    p.add_argument("--no-pause",  action="store_true",
                   help="Disable consecutive-SL and win-rate pause (signal quality mode)")
    p.add_argument("--no-ema",      action="store_true",
                   help="Disable EMA entry signals — run ORB only")
    p.add_argument("--ema-filter",  action="store_true",
                   help="Only take ORB LONG when EMA5>EMA20, ORB SHORT when EMA5<EMA20")
    p.add_argument("--atr-cap",    type=float, default=None,
                   help="Override orb_atr_cap_pts (e.g. 14)")
    p.add_argument("--capital",    type=float, default=None,
                   help="Override capital_usd for circuit-breaker thresholds (e.g. 8000)")
    p.add_argument("--orb-bars",  type=int, default=2,
                   help="Number of bars to build ORB range (2=10:15 cutoff, 3=10:30 cutoff)")
    p.add_argument("--ema-short-only", action="store_true",
                   help="Apply EMA filter to ORB SHORT only (LONG enters freely)")
    args = p.parse_args()

    if not Path(args.csv).exists():
        print(f"ERROR: CSV not found: {args.csv}")
        sys.exit(1)

    q   = args.quality if args.quality is not None else BAR_QUALITY_MIN
    cap = args.atr_cap if args.atr_cap is not None else ORB_ATR_CAP
    cap_usd = args.capital if args.capital is not None else _CAPITAL_DEFAULT
    run_replay(args.csv, args.verbose, quality_min=q, no_pause=args.no_pause,
               no_ema=args.no_ema, ema_filter=args.ema_filter, atr_cap=cap,
               capital=cap_usd, orb_bars=args.orb_bars,
               ema_short_only=args.ema_short_only)
