"""FastAPI dashboard server — runs in the same asyncio loop as the bot."""
from __future__ import annotations

import json
import pathlib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from dashboard.state import bot_state, ws_clients, broadcast, state_dict

app = FastAPI(title="MES Scalping Bot Dashboard")

_STATIC = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/state")
async def get_state():
    return state_dict()


@app.get("/trades")
async def get_trades():
    return bot_state.trade_history


@app.get("/graduation")
def graduation():
    """Compute paper→live graduation checklist from trades_paper.json."""
    import json
    from datetime import datetime
    from pathlib import Path

    try:
        trades = json.loads(Path("data/trades_paper.json").read_text())
    except Exception:
        trades = []

    total    = len(trades)
    wins     = sum(1 for t in trades if t.get("exit_reason") == "TP")
    win_rate = wins / total if total > 0 else 0.0

    # Weekly P&L
    weekly: dict = {}
    for t in trades:
        try:
            dt  = datetime.fromisoformat(t["entry_time"])
            key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
            weekly[key] = weekly.get(key, 0.0) + t["pnl"]
        except Exception:
            pass

    max_weekly_loss = min(weekly.values()) if weekly else 0.0

    # Consecutive profitable weeks (most recent streak)
    consec = 0
    for key in reversed(sorted(weekly.keys())):
        if weekly[key] > 0:
            consec += 1
        else:
            break

    # Max drawdown on cumulative P&L curve
    cum = peak = max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.get("entry_time", "")):
        cum += t.get("pnl", 0.0)
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    criteria = {
        "trades":       {"value": total,           "ok": total >= 200},
        "win_rate":     {"value": win_rate,        "ok": win_rate >= 0.47},
        "weekly_loss":  {"value": max_weekly_loss, "ok": max_weekly_loss > -150.0},
        "consec_weeks": {"value": consec,          "ok": consec >= 4},
        "drawdown":     {"value": max_dd,          "ok": max_dd <= 750.0},
    }
    return {"ready": all(c["ok"] for c in criteria.values()), "criteria": criteria}


@app.post("/pause")
async def pause():
    bot_state.paused       = True
    bot_state.pause_reason = "MANUAL"
    from datetime import datetime
    import pytz
    bot_state.paused_at = datetime.now(pytz.timezone("US/Eastern")).isoformat()
    await broadcast()
    return {"ok": True}


@app.post("/resume")
async def resume():
    bot_state.paused         = False
    bot_state.pause_reason   = ""
    bot_state.paused_at      = ""
    bot_state.consecutive_sl = 0
    # rolling_win_rate intentionally preserved
    await broadcast()
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        # Send current state immediately on connect
        await ws.send_text(json.dumps(state_dict()))
        while True:
            # Keep connection alive; disconnect detected by exception
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_clients.discard(ws)
