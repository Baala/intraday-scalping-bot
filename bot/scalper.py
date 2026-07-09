"""
MES 15-minute intraday scalping bot.
Mode-agnostic — paper vs live is a connection profile switch only.
Run via: python main.py --mode paper | live
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import sys
from collections import deque
from datetime import datetime, date, time, timedelta
from typing import Optional

import pytz
from ib_insync import IB, Future, MarketOrder, StopOrder, LimitOrder

from dashboard.state import bot_state, broadcast, PositionState

# ── Config ────────────────────────────────────────────────────────────────────

with open("config/scalping_config.json") as _f:
    CFG = json.load(_f)

ET             = pytz.timezone("US/Eastern")
POINT_VALUE    = CFG["point_value"]
COMMISSION     = CFG["commission_per_side_usd"]
MAX_DAILY_LOSS = CFG["capital_usd"] * 0.03
MAX_WEEKLY_LOSS = CFG["capital_usd"] * (CFG["max_weekly_loss_pct"] / 100.0)
ATR_PERIOD     = CFG["atr_period"]
ATR_SPIKE_MULT = CFG["atr_spike_mult"]
ADX_MULT       = 2.0 / (CFG["adx_period"] + 1)
EMA_FAST_MULT  = 2.0 / (CFG["ema_fast"] + 1)
EMA_SLOW_MULT  = 2.0 / (CFG["ema_slow"] + 1)
VWAP_ANCHOR    = time(9, 30)
MARKET_OPEN    = time(9, 45)
MARKET_CLOSE   = time(15, 30)
EOD_CLOSE_TIME = time(15, 30)
ORB_CUTOFF     = time(10, 0)   # range locked in after 10:00 ET; no late bars accepted

MES_RISK_BIN = str(pathlib.Path("build") / (
    "mes_risk.exe" if sys.platform == "win32" else "mes_risk"))

log = logging.getLogger("mes_scalper")

# ── Module-level running indicator state ─────────────────────────────────────

ema_fast: Optional[float] = None
ema_slow: Optional[float] = None
warmup_closes: deque = deque(maxlen=CFG["ema_slow"])

prev_ema_fast: Optional[float] = None
prev_ema_slow: Optional[float] = None

# VWAP
cumulative_tpv: float = 0.0
cumulative_vol: float = 0.0
vwap_date: Optional[date] = None

# ADX
adx_prev_high:  Optional[float] = None
adx_prev_low:   Optional[float] = None
adx_prev_close: Optional[float] = None
dm_plus_ema:    Optional[float] = None
dm_minus_ema:   Optional[float] = None
tr_ema:         Optional[float] = None
dx_ema:         Optional[float] = None

# ATR / OHLCV
ohlcv_history: deque = deque(maxlen=ATR_PERIOD + 1)
wilder_atr: Optional[float] = None  # Wilder's smoothed ATR state

# Volume
volumes: deque = deque(maxlen=20)

# Bar tracking
last_bar_time: Optional[datetime] = None
last_poll_time: Optional[datetime] = None  # when _bar_poller last succeeded
bars_received: int = 0
session_bar_start_date: Optional[datetime] = None  # first bar date after each reset

# Opening Range Breakout (ORB) — range built from first 2 RTH bars
ORB_RANGE_BARS = 2
orb_high: Optional[float] = None
orb_low:  Optional[float] = None
orb_bars_seen: int = 0      # post-warmup bars counted toward range
orb_traded_today: bool = False  # 1 ORB entry allowed per session

# Rolling win-rate tracker
recent_outcomes: deque = deque(maxlen=CFG["win_rate_lookback"])

# Regime history (loaded from disk on startup)
adx_block_history: list = []

# IB / contract references set in run_bot
_ib: Optional[IB] = None
_contract = None
_mode: str = "paper"

# ── Persistence helpers ───────────────────────────────────────────────────────

def _state_file() -> pathlib.Path:
    return pathlib.Path(f"data/bot_state_{_mode}.json")

def _trades_file() -> pathlib.Path:
    return pathlib.Path(f"data/trades_{_mode}.json")

def _regime_file() -> pathlib.Path:
    return pathlib.Path(f"data/regime_history_{_mode}.json")


def _load_json(path: pathlib.Path) -> Optional[dict | list]:
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def _save_state_snapshot() -> None:
    today = datetime.now(ET).date().isoformat()
    monday = _monday_of(datetime.now(ET)).date().isoformat()
    _save_json(_state_file(), {
        "date":       today,
        "week_start": monday,
        "daily_pnl":  bot_state.daily_pnl,
        "weekly_pnl": bot_state.weekly_pnl,
    })


def _load_trades() -> list:
    data = _load_json(_trades_file())
    return data if isinstance(data, list) else []


def _append_trade(record: dict) -> None:
    trades = _load_trades()
    trades.append(record)
    _save_json(_trades_file(), trades)


def _monday_of(dt: datetime) -> datetime:
    return dt - timedelta(days=dt.weekday())


def _save_regime_history() -> None:
    _save_json(_regime_file(), adx_block_history)


def _load_regime_history() -> None:
    global adx_block_history
    data = _load_json(_regime_file())
    adx_block_history = data if isinstance(data, list) else []


# ── Startup recovery ──────────────────────────────────────────────────────────

async def restore_daily_pnl() -> None:
    today   = datetime.now(ET).date().isoformat()
    monday  = _monday_of(datetime.now(ET)).date().isoformat()
    snap    = _load_json(_state_file())

    if snap and snap.get("date") == today:
        bot_state.daily_pnl  = snap["daily_pnl"]
        bot_state.weekly_pnl = snap["weekly_pnl"] if snap.get("week_start") == monday else 0.0
    else:
        trades = _load_trades()
        bot_state.daily_pnl  = sum(t["pnl"] for t in trades if t["entry_time"].startswith(today))
        bot_state.weekly_pnl = sum(t["pnl"] for t in trades if t["entry_time"] >= monday)

    bot_state.circuit_breaker_active = bot_state.daily_pnl  <= -MAX_DAILY_LOSS
    bot_state.weekly_cb_active       = bot_state.weekly_pnl <= -MAX_WEEKLY_LOSS
    bot_state.trade_history = [t for t in _load_trades() if t["entry_time"].startswith(today)]

    _load_regime_history()
    _update_regime_status()
    log.info(f"Restored daily_pnl=${bot_state.daily_pnl:.2f}  weekly_pnl=${bot_state.weekly_pnl:.2f}")


# ── Indicator functions ───────────────────────────────────────────────────────

def _reset_indicators() -> None:
    global cumulative_tpv, cumulative_vol, vwap_date
    global adx_prev_high, adx_prev_low, adx_prev_close
    global dm_plus_ema, dm_minus_ema, tr_ema, dx_ema
    global bars_received, session_bar_start_date
    global orb_high, orb_low, orb_bars_seen, orb_traded_today
    global wilder_atr

    # EMAs intentionally NOT reset — they were seeded from historical bars
    # and carry their value across sessions / brief connection gaps.
    # VWAP resets because it is an intraday daily metric.
    cumulative_tpv = cumulative_vol = 0.0
    vwap_date = None
    adx_prev_high = adx_prev_low = adx_prev_close = None
    dm_plus_ema = dm_minus_ema = tr_ema = dx_ema = None
    wilder_atr = None
    ohlcv_history.clear()
    orb_high = orb_low = None
    orb_bars_seen = 0
    orb_traded_today = False
    bars_received = 0
    session_bar_start_date = None
    log.info("Indicators reset")


def update_ema(close: float) -> None:
    global ema_fast, ema_slow, prev_ema_fast, prev_ema_slow
    warmup_closes.append(close)
    prev_ema_fast, prev_ema_slow = ema_fast, ema_slow

    if ema_fast is None:
        if len(warmup_closes) >= CFG["ema_fast"]:
            ema_fast = sum(list(warmup_closes)[-CFG["ema_fast"]:]) / CFG["ema_fast"]
    else:
        ema_fast = ema_fast + EMA_FAST_MULT * (close - ema_fast)

    if ema_slow is None:
        if len(warmup_closes) >= CFG["ema_slow"]:
            ema_slow = sum(warmup_closes) / CFG["ema_slow"]
    else:
        ema_slow = ema_slow + EMA_SLOW_MULT * (close - ema_slow)


def update_vwap(bar) -> Optional[float]:
    global cumulative_tpv, cumulative_vol, vwap_date
    bar_et   = bar.date.astimezone(ET)
    bar_date = bar_et.date()
    bar_time = bar_et.time()

    if bar_time < VWAP_ANCHOR:
        return None

    if vwap_date != bar_date:
        cumulative_tpv = 0.0
        cumulative_vol = 0.0
        vwap_date = bar_date

    tp = (bar.high + bar.low + bar.close) / 3.0
    cumulative_tpv += tp * bar.volume
    cumulative_vol += bar.volume
    return cumulative_tpv / cumulative_vol if cumulative_vol > 0 else None


def update_adx(bar) -> Optional[float]:
    global adx_prev_high, adx_prev_low, adx_prev_close
    global dm_plus_ema, dm_minus_ema, tr_ema, dx_ema

    if adx_prev_close is None:
        adx_prev_high, adx_prev_low, adx_prev_close = bar.high, bar.low, bar.close
        return None

    up   = bar.high - adx_prev_high
    down = adx_prev_low - bar.low
    dm_p = up   if (up > down and up > 0)   else 0.0
    dm_m = down if (down > up and down > 0) else 0.0

    tr = max(bar.high - bar.low,
             abs(bar.high - adx_prev_close),
             abs(bar.low  - adx_prev_close))

    adx_prev_high, adx_prev_low, adx_prev_close = bar.high, bar.low, bar.close

    def ema_up(prev, val):
        return val if prev is None else prev + ADX_MULT * (val - prev)

    dm_plus_ema  = ema_up(dm_plus_ema,  dm_p)
    dm_minus_ema = ema_up(dm_minus_ema, dm_m)
    tr_ema       = ema_up(tr_ema,       tr)

    if tr_ema == 0:
        return None

    di_p = 100.0 * dm_plus_ema  / tr_ema
    di_m = 100.0 * dm_minus_ema / tr_ema
    di_sum = di_p + di_m
    dx = 100.0 * abs(di_p - di_m) / di_sum if di_sum > 0 else 0.0
    dx_ema = ema_up(dx_ema, dx)
    return dx_ema


def update_atr() -> tuple[Optional[float], Optional[float]]:
    """Wilder's smoothed ATR — seeds on first ATR_PERIOD TRs, then recurses.
    ohlcv_history must already contain the current bar before calling."""
    global wilder_atr
    bars = list(ohlcv_history)
    if len(bars) < 2:
        return None, None
    current_tr = max(
        bars[-1].high - bars[-1].low,
        abs(bars[-1].high - bars[-2].close),
        abs(bars[-1].low  - bars[-2].close),
    )
    if wilder_atr is None:
        if len(bars) < ATR_PERIOD + 1:
            return None, current_tr
        # Seed: simple average of the first ATR_PERIOD true ranges
        trs = [
            max(bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i-1].close),
                abs(bars[i].low  - bars[i-1].close))
            for i in range(1, len(bars))
        ]
        wilder_atr = sum(trs) / len(trs)
    else:
        wilder_atr = (wilder_atr * (ATR_PERIOD - 1) + current_tr) / ATR_PERIOD
    return wilder_atr, current_tr


def volume_ok(vol: float) -> bool:
    if len(volumes) < 20:
        return True
    avg = sum(volumes) / len(volumes)
    return vol >= avg * (CFG["volume_filter_pct"] / 100.0)


def is_market_hours() -> bool:
    t = datetime.now(ET).time()
    return MARKET_OPEN <= t < MARKET_CLOSE


def in_sl_cooldown(bar_time: datetime) -> bool:
    if bot_state.sl_cooldown_bar_time is None:
        return False
    cooldown_start = datetime.fromisoformat(bot_state.sl_cooldown_bar_time)
    elapsed = (bar_time - cooldown_start).total_seconds()
    if elapsed < CFG["sl_cooldown_minutes"] * 60:
        return True
    bot_state.sl_cooldown_bar_time = None
    return False


# ── Regime tracking ───────────────────────────────────────────────────────────

def _update_regime_status() -> None:
    days = CFG["choppy_regime_days"]
    if len(adx_block_history) < days:
        bot_state.adx_block_rate_5d   = 0.0
        bot_state.choppy_regime_active = False
        return
    window       = adx_block_history[-days:]
    total_opps   = sum(d["opportunities"] for d in window)
    total_blocked = sum(d["blocked"]      for d in window)
    rate = (total_blocked / total_opps) if total_opps > 0 else 0.0
    bot_state.adx_block_rate_5d   = rate
    bot_state.choppy_regime_active = rate > CFG["choppy_regime_threshold"]

    if bot_state.choppy_regime_active and not bot_state.paused:
        bot_state.paused       = True
        bot_state.pause_reason = "CHOPPY_REGIME"
        bot_state.paused_at    = datetime.now(ET).isoformat()
        log.warning(f"AUTO-PAUSE: choppy regime — ADX blocked {rate:.0%} of signals over {days} days")
        asyncio.ensure_future(broadcast())


# ── Daily reset loop ──────────────────────────────────────────────────────────

async def daily_reset_loop() -> None:
    global adx_block_history

    while True:
        now   = datetime.now(ET)
        next_ = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=1, microsecond=0)
        await asyncio.sleep((next_ - now).total_seconds())

        today_str = now.date().isoformat()

        # Archive today's ADX block counts before reset
        if bot_state.daily_signal_opps > 0:
            adx_block_history.append({
                "date":          today_str,
                "opportunities": bot_state.daily_signal_opps,
                "blocked":       bot_state.daily_adx_blocked,
            })
            max_days = CFG["choppy_regime_days"]
            if len(adx_block_history) > max_days:
                adx_block_history = adx_block_history[-max_days:]
            _save_regime_history()

        # Reset daily counters
        bot_state.daily_pnl            = 0.0
        bot_state.circuit_breaker_active = False
        bot_state.daily_signal_opps    = 0
        bot_state.daily_adx_blocked    = 0
        bot_state.reconnect_count      = 0

        # Reset weekly P&L on Monday
        if datetime.now(ET).weekday() == 0:
            bot_state.weekly_pnl    = 0.0
            bot_state.weekly_cb_active = False

        _update_regime_status()
        log.info("Daily reset complete")
        asyncio.ensure_future(broadcast())


# ── C++ risk call ─────────────────────────────────────────────────────────────

async def call_cpp_risk(price: float, sl_points: Optional[float] = None) -> dict:
    effective_sl = sl_points if sl_points is not None else CFG["stop_loss_points"]
    proc = await asyncio.create_subprocess_exec(
        MES_RISK_BIN,
        "--entry",       str(price),
        "--capital",     str(CFG["capital_usd"]),
        "--risk-pct",    str(CFG["risk_pct"]),
        "--sl-points",   str(effective_sl),
        "--point-value", str(CFG["point_value"]),
        "--rr",          str(CFG["reward_ratio"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("mes_risk timed out")
    return json.loads(stdout.decode())


# ── Order helpers ─────────────────────────────────────────────────────────────

async def _wait_for_fill(trade, timeout: float = 10.0) -> Optional[float]:
    """Return fill price or None on timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if trade.orderStatus.status == "Filled":
            return trade.orderStatus.avgFillPrice
        await asyncio.sleep(0.1)
    return None


# ── Handle BUY ────────────────────────────────────────────────────────────────

async def handle_buy(close_price: float, signal_type: str = "") -> None:
    global orb_traded_today
    ib, contract = _ib, _contract

    # ORB entries use 1×ATR as stop distance for breathing room
    sl_pts: Optional[float] = None
    if signal_type == "ORB" and bot_state.current_atr > 0:
        tick = CFG["tick_size"]
        sl_pts = max(round(bot_state.current_atr / tick) * tick, CFG["stop_loss_points"])
        log.info(f"ORB entry: ATR-based SL={sl_pts:.2f} pts (ATR={bot_state.current_atr:.2f})")

    # 1. Size via C++
    risk = await call_cpp_risk(close_price, sl_pts)
    if risk.get("error") or risk.get("contracts", 0) < 1:
        log.warning(f"Risk check: {risk}")
        return

    max_c = CFG["max_contracts_paper"] if _mode == "paper" else CFG["max_contracts_live"]
    contracts = min(risk["contracts"], max_c)

    # 2. Entry MKT
    entry_order = MarketOrder("BUY", contracts)
    entry_trade = ib.placeOrder(contract, entry_order)
    log.info(f"BUY {contracts}x MES at ~{close_price:.2f}")

    filled_price = await _wait_for_fill(entry_trade)
    if filled_price is None:
        log.warning("Entry fill timeout — cancelling")
        ib.cancelOrder(entry_order)
        return

    # 3. Recompute SL/TP from fill (R1 — preserves true stop distance)
    risk2 = await call_cpp_risk(filled_price, sl_pts)
    sl = risk2["stop_loss"]
    tp = risk2["take_profit"]

    # 4. Bracket: SL stop + TP limit (OCA)
    oca = f"MES_OCA_{entry_trade.order.orderId}"
    sl_order = StopOrder("SELL", contracts, sl, ocaGroup=oca, ocaType=1, transmit=False)
    tp_order = LimitOrder("SELL", contracts, tp, ocaGroup=oca, ocaType=1, transmit=True)

    sl_trade = ib.placeOrder(contract, sl_order)
    tp_trade = ib.placeOrder(contract, tp_order)

    # 5. Register exit handlers
    entry_time_str = datetime.now(ET).isoformat()

    def _on_exit(_, fill):
        asyncio.ensure_future(_process_exit(fill.execution.avgPrice, entry_time_str, filled_price, tp, contracts))

    sl_trade.fillEvent += _on_exit
    tp_trade.fillEvent += _on_exit

    # 6. Update state
    bot_state.in_trade = True
    bot_state.position = PositionState(contracts, filled_price, sl, tp)
    if signal_type == "ORB":
        orb_traded_today = True  # one ORB entry per session
    log.info(f"Position open: entry={filled_price:.2f}  SL={sl:.2f}  TP={tp:.2f}  contracts={contracts}")
    asyncio.ensure_future(broadcast())


async def _process_exit(exit_price: float, entry_time: str, entry_price: float,
                        tp: float, contracts: int) -> None:
    if not bot_state.in_trade:
        return  # duplicate fill event guard

    tp_price_captured = tp
    pnl = (exit_price - entry_price) * POINT_VALUE * contracts - COMMISSION * 2 * contracts
    reason = "TP" if exit_price >= tp_price_captured else "SL"

    bot_state.daily_pnl              += pnl
    bot_state.weekly_pnl             += pnl
    bot_state.circuit_breaker_active  = bot_state.daily_pnl  <= -MAX_DAILY_LOSS
    bot_state.weekly_cb_active        = bot_state.weekly_pnl <= -MAX_WEEKLY_LOSS
    bot_state.in_trade                = False
    bot_state.position                = PositionState()

    # Degradation trackers
    recent_outcomes.append(reason == "TP")
    if reason == "SL":
        bot_state.sl_cooldown_bar_time = last_bar_time.isoformat() if last_bar_time else datetime.now(ET).isoformat()
        bot_state.consecutive_sl += 1
        if bot_state.consecutive_sl == CFG["consecutive_sl_warn"]:
            log.warning(f"{CFG['consecutive_sl_warn']} consecutive stop-losses — watching for pattern")
        if bot_state.consecutive_sl >= CFG["consecutive_sl_pause"] and not bot_state.paused:
            bot_state.paused       = True
            bot_state.pause_reason = "CONSECUTIVE_SL_5"
            bot_state.paused_at    = datetime.now(ET).isoformat()
            log.error("AUTO-PAUSE: 5 consecutive stop-losses")
    else:
        bot_state.consecutive_sl = 0

    if len(recent_outcomes) >= 5:
        wins = sum(recent_outcomes)
        bot_state.rolling_win_rate      = wins / len(recent_outcomes)
        bot_state.rolling_win_rate_warn = bot_state.rolling_win_rate < (CFG["win_rate_warn_pct"] / 100.0)

    if len(recent_outcomes) == CFG["win_rate_lookback"] and not bot_state.paused:
        if bot_state.rolling_win_rate < (CFG["win_rate_pause_pct"] / 100.0):
            bot_state.paused       = True
            bot_state.pause_reason = "WIN_RATE_LOW"
            bot_state.paused_at    = datetime.now(ET).isoformat()
            log.error(f"AUTO-PAUSE: rolling win rate {bot_state.rolling_win_rate:.1%} < {CFG['win_rate_pause_pct']}%")

    record = {
        "entry_time":  entry_time,
        "exit_time":   datetime.now(ET).isoformat(),
        "entry_price": entry_price,
        "exit_price":  exit_price,
        "contracts":   contracts,
        "pnl":         pnl,
        "exit_reason": reason,
    }
    bot_state.trade_history.append(record)
    _save_state_snapshot()
    _append_trade(record)

    log.info(f"EXIT {reason}  exit={exit_price:.2f}  pnl=${pnl:.2f}  daily=${bot_state.daily_pnl:.2f}")
    asyncio.ensure_future(broadcast())


# ── EOD sweep ────────────────────────────────────────────────────────────────

async def eod_monitor_loop() -> None:
    while True:
        await asyncio.sleep(30)
        if datetime.now(ET).time() >= EOD_CLOSE_TIME:
            await eod_close_and_sweep()
            break


async def eod_close_and_sweep() -> None:
    ib, contract = _ib, _contract
    log.info("EOD 15:30 sweep starting")

    # Cancel all open MES orders first
    cancelled = []
    for trade in ib.openTrades():
        if trade.contract.symbol == "MES":
            ib.cancelOrder(trade.order)
            cancelled.append(trade.order.orderId)
    if cancelled:
        log.info(f"EOD cancelled {len(cancelled)} orders: {cancelled}")
        await asyncio.sleep(1)

    # MKT close if still in trade
    if bot_state.in_trade:
        log.info("EOD forced close")
        close_order = MarketOrder("SELL", bot_state.position.contracts)
        ib.placeOrder(contract, close_order)

    # Safety net — if fill doesn't arrive in 60s, force state reset
    await asyncio.sleep(60)
    if bot_state.in_trade:
        log.error("EOD: position still open 60s after close — forcing state reset")
        bot_state.in_trade = False
        bot_state.position = PositionState()
        asyncio.ensure_future(broadcast())


# ── Position reconciliation ──────────────────────────────────────────────────

async def reconcile_position(ib: IB, contract) -> None:
    positions = await ib.reqPositionsAsync()
    mes_pos = next((p for p in positions if p.contract.symbol == "MES"), None)
    if mes_pos and mes_pos.position != 0:
        qty = int(abs(mes_pos.position))
        log.warning(f"Open position on startup: {qty} contracts — placing protective close")
        bot_state.in_trade = True
        bot_state.position.contracts = qty
        ib.placeOrder(contract, MarketOrder("SELL", qty))


# ── Bar processing ────────────────────────────────────────────────────────────

async def process_15min_bar(bar) -> None:
    global last_bar_time, bars_received, session_bar_start_date
    global orb_high, orb_low, orb_bars_seen

    # ── Bar gap detection (R13) ──
    if last_bar_time is not None:
        gap = (bar.date - last_bar_time).total_seconds()
        if gap > CFG["bar_gap_threshold_sec"]:
            log.warning(f"Bar gap {gap/60:.1f} min — resetting indicators, warming up {CFG['warmup_bars_after_gap']} bars")
            _reset_indicators()
            bot_state.warming_up           = True
            bot_state.warmup_bars_remaining = CFG["warmup_bars_after_gap"]
            asyncio.ensure_future(broadcast())

    last_bar_time = bar.date

    # ── Bar quality ──
    bars_received += 1
    bar_et = bar.date.astimezone(ET)
    if session_bar_start_date is None:
        session_bar_start_date = bar.date
    elapsed_15min = max(1, (bar.date - session_bar_start_date).total_seconds() / 900 + 1)
    bot_state.bar_quality_pct = min(100.0, (bars_received / elapsed_15min) * 100.0)

    # ── Warmup countdown ──
    if bot_state.warming_up:
        bot_state.warmup_bars_remaining -= 1
        if bot_state.warmup_bars_remaining <= 0:
            bot_state.warming_up = False
            log.info("Warmup complete — signal processing resumed")
        asyncio.ensure_future(broadcast())
        # Feed indicators during warmup so they converge
        update_ema(bar.close)
        ohlcv_history.append(bar)
        volumes.append(bar.volume)
        update_adx(bar)
        update_vwap(bar)
        # Build ORB range during warmup bars — only accepts bars before 10:00 ET
        if orb_bars_seen < ORB_RANGE_BARS and bar_et.time() < ORB_CUTOFF:
            orb_bars_seen += 1
            orb_high = max(orb_high, bar.high) if orb_high is not None else bar.high
            orb_low  = min(orb_low,  bar.low)  if orb_low  is not None else bar.low
            if orb_bars_seen == ORB_RANGE_BARS:
                log.info(f"ORB range set: high={orb_high:.2f}  low={orb_low:.2f}")
        return

    # ── Indicator updates ──
    update_ema(bar.close)
    ohlcv_history.append(bar)
    volumes.append(bar.volume)

    current_vwap = update_vwap(bar)
    adx          = update_adx(bar)
    atr, current_tr = update_atr()

    # ── Update BotState display fields ──
    bot_state.current_price        = bar.close
    bot_state.ema_fast             = ema_fast or 0.0
    bot_state.ema_slow             = ema_slow or 0.0
    bot_state.vwap                 = current_vwap or 0.0
    bot_state.current_atr          = atr or 0.0
    bot_state.current_tr           = current_tr or 0.0
    bot_state.current_adx          = adx or 0.0
    bot_state.vwap_filter_active   = (current_vwap is not None and bar.close < current_vwap)
    bot_state.atr_filter_active    = (
        atr is not None and current_tr is not None and
        current_tr > ATR_SPIKE_MULT * atr
    )
    bot_state.adx_filter_active    = (adx is not None and adx < CFG["adx_min"])
    bot_state.volume_filter_active = not volume_ok(bar.volume)

    # ── ORB range building (post-warmup, in case warmup < ORB_RANGE_BARS) ──
    # Hard cutoff at 10:00 ET — late-morning bars never update the opening range
    if orb_bars_seen < ORB_RANGE_BARS and bar_et.time() < ORB_CUTOFF:
        orb_bars_seen += 1
        orb_high = max(orb_high, bar.high) if orb_high is not None else bar.high
        orb_low  = min(orb_low,  bar.low)  if orb_low  is not None else bar.low
        if orb_bars_seen == ORB_RANGE_BARS:
            log.info(f"ORB range set: high={orb_high:.2f}  low={orb_low:.2f}")

    # ── EMA crossover signal ──
    signal      = "HOLD"
    signal_type = ""
    if ema_fast is not None and ema_slow is not None and \
       prev_ema_fast is not None and prev_ema_slow is not None:
        if prev_ema_fast <= prev_ema_slow and ema_fast > ema_slow:
            signal = "BUY";  signal_type = "EMA"
        elif prev_ema_fast >= prev_ema_slow and ema_fast < ema_slow:
            signal = "SELL"; signal_type = "EMA"

    # ── ORB breakout signal (only after range is established; 1 entry per session) ──
    if signal == "HOLD" and orb_bars_seen >= ORB_RANGE_BARS and orb_high and orb_low and not orb_traded_today:
        if bar.close > orb_high:
            signal = "BUY";  signal_type = "ORB"
        elif bar.close < orb_low:
            signal = "SELL"; signal_type = "ORB"

    # VWAP filter on BUY only
    if signal == "BUY" and bot_state.vwap_filter_active:
        signal = "HOLD"; signal_type = ""

    if signal != "HOLD":
        log.info(f"{signal_type} {signal} signal @ {bar.close:.2f}  "
                 f"EMA({ema_fast:.2f}/{ema_slow:.2f})  "
                 f"ORB({orb_high:.2f}/{orb_low:.2f})" if orb_high else
                 f"{signal_type} {signal} signal @ {bar.close:.2f}")

    bot_state.signal      = signal
    bot_state.signal_type = signal_type
    bot_state.orb_high    = orb_high
    bot_state.orb_low     = orb_low
    asyncio.ensure_future(broadcast())

    # ── Count ADX-blockable opportunities (R14) ──
    would_enter = (
        signal == "BUY" and not bot_state.in_trade and
        is_market_hours() and not bot_state.warming_up and
        bot_state.bar_quality_pct >= CFG["bar_quality_min_pct"] and
        not bot_state.circuit_breaker_active and not bot_state.weekly_cb_active and
        not bot_state.atr_filter_active and not bot_state.volume_filter_active and
        not bot_state.paused and not in_sl_cooldown(bar.date)
    )
    if would_enter:
        bot_state.daily_signal_opps += 1
        if bot_state.adx_filter_active:
            bot_state.daily_adx_blocked += 1

    # ── Per-bar debug snapshot (always) ──
    vol_avg = (sum(volumes) / len(volumes)) if volumes else 0
    vol_ratio = (bar.volume / vol_avg * 100) if vol_avg > 0 else 0
    log.debug(
        f"BAR {bar_et.strftime('%H:%M')}  close={bar.close:.2f}  "
        f"EMA({ema_fast:.2f}/{ema_slow:.2f})  sig={signal}  "
        f"ADX={adx:.1f}  VWAP={current_vwap:.2f}  "
        f"vol={vol_ratio:.0f}%  TR={current_tr:.2f}/ATR={atr:.2f}"
        if ema_fast and ema_slow and adx and current_vwap and atr and current_tr else
        f"BAR {bar_et.strftime('%H:%M')}  close={bar.close:.2f}  sig={signal}  (warming up indicators)"
    )

    # ── Pre-condition gate — log reason when a BUY is blocked ──
    def _blocked(reason: str) -> bool:
        if signal == "BUY":
            log.info(f"BUY blocked [{bar_et.strftime('%H:%M')}]: {reason}")
        return True

    if not is_market_hours():
        return
    if bot_state.warming_up:
        if signal == "BUY": log.info(f"BUY blocked [{bar_et.strftime('%H:%M')}]: warming up")
        return
    if bot_state.bar_quality_pct < CFG["bar_quality_min_pct"]:
        if _blocked(f"bar quality {bot_state.bar_quality_pct:.0f}% < {CFG['bar_quality_min_pct']}%"): return
    if bot_state.circuit_breaker_active:
        if _blocked("daily circuit breaker active"): return
    if bot_state.weekly_cb_active:
        if _blocked("weekly circuit breaker active"): return
    if bot_state.adx_filter_active:
        if _blocked(f"ADX={adx:.1f} < {CFG['adx_min']} (no trend)"): return
    if bot_state.atr_filter_active:
        if _blocked(f"ATR spike TR={current_tr:.2f} > {ATR_SPIKE_MULT}×ATR={atr:.2f}"): return
    if bot_state.volume_filter_active:
        if _blocked(f"volume {vol_ratio:.0f}% < {CFG['volume_filter_pct']}% of avg"): return
    if bot_state.paused:
        if _blocked(f"auto-paused ({bot_state.pause_reason})"): return
    if in_sl_cooldown(bar.date):
        if _blocked("SL cooldown active"): return

    if signal == "BUY" and not bot_state.in_trade:
        await handle_buy(bar.close, signal_type)
    elif signal == "SELL" and bot_state.in_trade:
        # EMA cross-down: cancel bracket and MKT close
        log.info("EMA crossover SELL — closing position")
        if _ib and _contract:
            for trade in _ib.openTrades():
                if trade.contract.symbol == "MES":
                    _ib.cancelOrder(trade.order)
            await asyncio.sleep(0.5)
            _ib.placeOrder(_contract, MarketOrder("SELL", bot_state.position.contracts))


# ── Main trading loop ────────────────────────────────────────────────────────

async def _check_cpp_binary() -> None:
    """Verify mes_risk.exe works at startup — fail fast before any signal fires."""
    if not pathlib.Path(MES_RISK_BIN).exists():
        raise FileNotFoundError(
            f"C++ binary not found: {MES_RISK_BIN}\n"
            "  Run: Ctrl+Shift+B in VS Code  OR\n"
            "  C:\\msys64\\ucrt64\\bin\\g++ -std=c++17 -O2 -Icore "
            "core/backtest_main.cpp core/backtest/BacktestEngine.cpp "
            "core/risk/RiskManager.cpp -o build/backtest.exe"
        )
    try:
        result = await call_cpp_risk(5000.0)
        contracts = result.get("contracts", 0)
        log.info(f"C++ risk check OK — test call returned {contracts} contract(s)")
    except Exception as exc:
        raise RuntimeError(f"C++ risk binary failed self-test: {exc}") from exc


async def run_trading_loop(ib: IB, contract) -> None:
    global _ib, _contract

    _ib      = ib
    _contract = contract

    await _check_cpp_binary()

    hist = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime='',
        durationStr='2 D',
        barSizeSetting='15 mins',
        whatToShow='TRADES',
        useRTH=True,
        formatDate=2,
        keepUpToDate=False,
    )

    # Seed indicators from historical bars without generating signals
    async def _seed_indicators() -> None:
        global last_bar_time, bars_received
        global orb_high, orb_low, orb_bars_seen, session_bar_start_date
        today = datetime.now(ET).date()
        today_count = 0
        for bar in hist:
            update_ema(bar.close)
            ohlcv_history.append(bar)
            volumes.append(bar.volume)
            update_adx(bar)
            bar_et = bar.date.astimezone(ET)
            if bar_et.date() == today:
                update_vwap(bar)
                today_count += 1
                if session_bar_start_date is None:
                    session_bar_start_date = bar.date
                # Build ORB range from the first ORB_RANGE_BARS of today
                if orb_bars_seen < ORB_RANGE_BARS:
                    orb_bars_seen += 1
                    orb_high = max(orb_high, bar.high) if orb_high is not None else bar.high
                    orb_low  = min(orb_low,  bar.low)  if orb_low  is not None else bar.low
                    if orb_bars_seen == ORB_RANGE_BARS:
                        log.info(f"ORB range seeded: high={orb_high:.2f}  low={orb_low:.2f}")
        if hist:
            last_bar_time = hist[-1].date
            bot_state.current_price = hist[-1].close
            bot_state.ema_fast = ema_fast or 0.0
            bot_state.ema_slow = ema_slow or 0.0
        bars_received = today_count
        log.info(f"Indicators seeded — {len(hist)} historical bars, {today_count} today's bars")

    await _seed_indicators()
    asyncio.ensure_future(eod_monitor_loop())

    log.info(f"Polling MES 15-min bars (mode={_mode}). Waiting for signals...")

    async def _connection_monitor() -> None:
        while ib.isConnected():
            await asyncio.sleep(1)

    async def _bar_watchdog() -> None:
        """Detect Error 10182: bar stream dies silently while Gateway stays connected."""
        BAR_TIMEOUT = 22 * 60  # 22 min — one full bar interval plus 7 min buffer
        CHECK_EVERY = 60
        stream_start = datetime.now(ET)  # when THIS connection's stream started
        global last_poll_time
        last_poll_time = None  # reset so watchdog uses stream_start, not yesterday's timestamp
        while ib.isConnected():
            await asyncio.sleep(CHECK_EVERY)
            if not is_market_hours():
                continue
            now_et = datetime.now(ET)
            # Use last_poll_time (wall-clock when poller ran) not last_bar_time
            # (bar open time) — avoids false triggers when bar.date < stream_start
            reference = last_poll_time if last_poll_time is not None else stream_start
            elapsed = (now_et - reference).total_seconds()
            if elapsed > BAR_TIMEOUT:
                log.error(
                    f"Bar stream watchdog: no bar for {elapsed/60:.0f} min during RTH "
                    f"— forcing reconnect (likely Error 10182)"
                )
                raise RuntimeError("Bar stream dead — watchdog triggered")

    async def _premarket_health_check() -> None:
        """At 09:30 ET each day, ping IB to confirm connection is alive before RTH opens."""
        PREMARKET_CHECK = time(9, 30)
        checked_today: set = set()
        while ib.isConnected():
            await asyncio.sleep(30)
            now_et = datetime.now(ET)
            today  = now_et.date()
            if now_et.time() < PREMARKET_CHECK or today in checked_today:
                continue
            if now_et.time() >= MARKET_OPEN:
                checked_today.add(today)
                continue  # past window — mark done without checking
            # 09:30–09:45 window: ping IB
            checked_today.add(today)
            try:
                await asyncio.wait_for(ib.reqCurrentTimeAsync(), timeout=5.0)
                log.info("Pre-market health check OK — connection alive, ready for RTH")
            except Exception as exc:
                log.error(f"Pre-market health check FAILED ({exc}) — reconnecting before RTH")
                ib.disconnect()

    async def _bar_poller() -> None:
        """Fetch completed 15-min bars at each boundary — works without real-time subscription."""
        BAR_SIZE_MINS = 15
        SETTLE_SECS   = 10  # wait after boundary for bar to finalize on IB's end
        while ib.isConnected():
            try:
                now_et       = datetime.now(ET)
                mins_past    = now_et.minute % BAR_SIZE_MINS
                secs_to_next = (BAR_SIZE_MINS - mins_past) * 60 - now_et.second + SETTLE_SECS
                await asyncio.sleep(max(1, secs_to_next))
                if not is_market_hours():
                    continue
                try:
                    fresh = await asyncio.wait_for(
                        ib.reqHistoricalDataAsync(
                            contract,
                            endDateTime='',
                            durationStr='3600 S',
                            barSizeSetting='15 mins',
                            whatToShow='TRADES',
                            useRTH=True,
                            formatDate=2,
                            keepUpToDate=False,
                        ),
                        timeout=15.0,
                    )
                    if fresh:
                        global last_poll_time
                        last_poll_time = datetime.now(ET)
                        await process_15min_bar(fresh[-1])
                except asyncio.TimeoutError:
                    log.warning("Bar poll timed out — will retry next boundary")
                except Exception as exc:
                    log.error(f"Bar poll failed: {exc}")
            except asyncio.CancelledError:
                raise  # let gather() handle cancellation normally
            except BaseException as exc:
                log.error(f"Bar poller unexpected error: {exc} — continuing")
                await asyncio.sleep(5)

    # Run all four monitors; any exit or raise triggers reconnect
    await asyncio.gather(
        _connection_monitor(),
        _bar_watchdog(),
        _premarket_health_check(),
        _bar_poller(),
        return_exceptions=False,
    )


# ── Top-level bot entry ──────────────────────────────────────────────────────

async def run_bot(mode: str) -> None:
    global _mode
    _mode = mode
    bot_state.mode = mode

    conn          = CFG[mode]
    reconnect_max = CFG["max_reconnects_per_session"]
    reconnect_delay = CFG["reconnect_delay_sec"]
    _client_id_offset = 0  # bumped automatically on Error 326 (clientId in use)
    _clientid_conflict = [False]  # mutable flag set by error handler closure

    await restore_daily_pnl()
    asyncio.ensure_future(daily_reset_loop())

    while True:
        _clientid_conflict[0] = False
        try:
            ib = IB()

            # Silence ib_insync's own error logger so our handler is the sole logger
            logging.getLogger("ib_insync.wrapper").setLevel(logging.CRITICAL)

            def _on_ib_error(_, errorCode, errorString, __=None):
                SILENT = {322}  # 322: duplicate account-summary on reconnect — harmless
                if errorCode in SILENT:
                    return
                if errorCode == 326:
                    _clientid_conflict[0] = True
                    log.warning(f"ClientId {client_id} already in use — will retry with {client_id + 1}")
                    ib.disconnect()
                    return
                level = logging.ERROR if errorCode < 2000 else logging.INFO
                log.log(level, f"IB {errorCode}: {errorString}")
                if errorCode == 10182:
                    log.error("IB 10182: bar stream dead — disconnecting to trigger reconnect")
                    ib.disconnect()

            ib.errorEvent += _on_ib_error

            client_id = conn["ib_client_id"] + _client_id_offset
            await ib.connectAsync(conn["ib_host"], conn["ib_port"],
                                  clientId=client_id)
            _client_id_offset = 0  # reset on successful connect
            bot_state.connected = True
            bot_state.reconnect_count += 1

            if bot_state.reconnect_count > 1:
                log.info(f"Reconnect #{bot_state.reconnect_count}")
                if bot_state.reconnect_count > reconnect_max and not bot_state.paused:
                    bot_state.paused       = True
                    bot_state.pause_reason = "UNSTABLE_CONNECTION"
                    bot_state.paused_at    = datetime.now(ET).isoformat()
                    log.error(f"AUTO-PAUSE: {bot_state.reconnect_count} reconnects this session")

            asyncio.ensure_future(broadcast())

            # Qualify front-month contract — pick nearest expiry still active
            raw     = Future(symbol=CFG["symbol"], exchange=CFG["exchange"],
                             currency=CFG["currency"])
            details = await ib.reqContractDetailsAsync(raw)
            today   = datetime.now().strftime("%Y%m%d")
            active  = sorted(
                [d for d in details if d.contract.lastTradeDateOrContractMonth >= today],
                key=lambda d: d.contract.lastTradeDateOrContractMonth,
            )
            if not active:
                raise RuntimeError("No active MES contracts found — contract may have expired")
            contract = active[0].contract
            log.info(f"Contract: {contract.localSymbol}  expiry={contract.lastTradeDateOrContractMonth}")

            # Reconcile any open position from a previous crash
            await reconcile_position(ib, contract)

            # Run until disconnect
            await run_trading_loop(ib, contract)

        except Exception as e:
            log.error(f"Bot error: {e}")
            if _clientid_conflict[0]:
                _client_id_offset = (_client_id_offset % 3) + 1
        finally:
            bot_state.connected = False
            asyncio.ensure_future(broadcast())

        log.info(f"Reconnecting in {reconnect_delay}s...")
        await asyncio.sleep(reconnect_delay)
