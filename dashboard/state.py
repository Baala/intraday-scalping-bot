"""
Shared bot state — module-level singleton imported by both scalper.py and server.py.
Safe without locks because both run in the same asyncio event loop.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class PositionState:
    contracts:   int   = 0
    entry_price: float = 0.0
    sl_price:    float = 0.0
    tp_price:    float = 0.0


@dataclass
class BotState:
    # Identity
    mode:                   str   = "paper"   # "paper" | "live"

    # Connection
    connected:              bool  = False
    reconnect_count:        int   = 0
    bar_quality_pct:        float = 100.0

    # Prices & indicators
    current_price:          float = 0.0
    ema_fast:               float = 0.0
    ema_slow:               float = 0.0
    vwap:                   float = 0.0
    current_atr:            float = 0.0
    current_tr:             float = 0.0
    current_adx:            float = 0.0

    # Filters
    vwap_filter_active:     bool  = False
    atr_filter_active:      bool  = False
    adx_filter_active:      bool  = False
    volume_filter_active:   bool  = False

    # Signal
    signal:                 str   = "HOLD"
    signal_type:            str   = ""    # "EMA" | "ORB" | ""

    # ORB levels
    orb_high:               Optional[float] = None
    orb_low:                Optional[float] = None

    # Trade
    in_trade:               bool  = False
    position_direction:     str   = "LONG"   # "LONG" | "SHORT"
    position: PositionState = field(default_factory=PositionState)

    # P&L
    daily_pnl:              float = 0.0
    weekly_pnl:             float = 0.0

    # Circuit breakers
    circuit_breaker_active: bool  = False
    weekly_cb_active:       bool  = False

    # Pause / auto-pause
    paused:                 bool  = False
    pause_reason:           str   = ""    # MANUAL | CONSECUTIVE_SL_5 | WIN_RATE_LOW | UNSTABLE_CONNECTION | CHOPPY_REGIME
    paused_at:              str   = ""

    # Degradation trackers
    consecutive_sl:         int   = 0
    rolling_win_rate:       float = 0.0
    rolling_win_rate_warn:  bool  = False

    # Warmup
    warming_up:             bool  = False
    warmup_bars_remaining:  int   = 0

    # SL cooldown (stored as ISO string; None = no cooldown active)
    sl_cooldown_bar_time:   Optional[str] = None

    # Choppy regime
    daily_signal_opps:      int   = 0
    daily_adx_blocked:      int   = 0
    adx_block_rate_5d:      float = 0.0
    choppy_regime_active:   bool  = False

    # Trade history (today only, for dashboard)
    trade_history:          list  = field(default_factory=list)


def _default_bot_state() -> BotState:
    return BotState()


bot_state:  BotState   = BotState()
ws_clients: set        = set()


def state_dict() -> dict:
    """Return bot_state as a JSON-serialisable dict."""
    d = asdict(bot_state)
    # datetime fields are stored as ISO strings; already serialisable
    return d


async def broadcast() -> None:
    """Push current state to all connected WebSocket clients."""
    global ws_clients
    payload = json.dumps(state_dict())
    dead: set = set()
    for ws in ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    ws_clients -= dead
