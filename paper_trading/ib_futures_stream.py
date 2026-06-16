"""
MES Intraday Scalping — IB TWS Paper Trading
Strategy: MA(5)/MA(20) crossover on 1-minute bars
Risk:      1% = $10/trade | 2-pt stop ($10) | 4-pt target ($20) | max 1 contract
Net P&L:   +$18.30 win / -$11.70 loss after $1.70 round-trip commission

Run:
  python paper_trading/ib_futures_stream.py

Requires:
  pip install ib_insync pytz
  IB Gateway running on 127.0.0.1:7497 (paper trading mode)
  C++ risk binary built at ./build/mes_risk
"""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, time
import pytz

from ib_insync import IB, Future, BarDataList, LimitOrder, StopOrder, Order

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("mes_scalper")

# ── Config ───────────────────────────────────────────────────────────────────
with open("config/scalping_config.json") as f:
    CFG = json.load(f)

POINT_VALUE    = CFG["point_value"]              # $5.00 per point
RISK_USD       = CFG["capital_usd"] * (CFG["risk_pct"] / 100.0)  # $10
SL_POINTS      = CFG["stop_loss_points"]         # 2.0
TP_POINTS      = CFG["take_profit_points"]       # 4.0
MA_FAST        = CFG["ma_fast"]                  # 5
MA_SLOW        = CFG["ma_slow"]                  # 20
MAX_CONTRACTS  = CFG["max_contracts"]            # 1
MAX_DAILY_LOSS = CFG["max_daily_loss_usd"]       # $30
COMMISSION     = CFG["commission_per_side_usd"]  # $0.85/side = $1.70 round trip
ET             = pytz.timezone("US/Eastern")
MARKET_OPEN    = time(9, 30)
MARKET_CLOSE   = time(15, 45)

# ── State ────────────────────────────────────────────────────────────────────
closes      = deque(maxlen=MA_SLOW + 5)  # rolling 1-min close prices
in_trade    = False
daily_pnl   = 0.0
entry_price = 0.0


def is_market_hours() -> bool:
    now_et = datetime.now(ET).time()
    return MARKET_OPEN <= now_et <= MARKET_CLOSE


def ma(prices, period: int) -> float | None:
    if len(prices) < period:
        return None
    return sum(list(prices)[-period:]) / period


def check_signal() -> str:
    fast = ma(closes, MA_FAST)
    slow = ma(closes, MA_SLOW)
    if fast is None or slow is None:
        return "HOLD"
    if fast > slow:
        return "BUY"
    if fast < slow:
        return "SELL"
    return "HOLD"


# ── Async C++ risk call ───────────────────────────────────────────────────────
# NEVER use subprocess.run() here — it blocks the asyncio event loop and
# freezes the IB WebSocket stream, causing missed market bars.
# asyncio.create_subprocess_exec() runs the binary without blocking the loop.
async def call_cpp_risk(close_price: float) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "./build/mes_risk",
        "--entry",     str(close_price),
        "--capital",   str(CFG["capital_usd"]),
        "--risk-pct",  str(CFG["risk_pct"]),
        "--sl-points", str(CFG["stop_loss_points"]),
        "--rr",        str(CFG["reward_ratio"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return json.loads(stdout.decode())
    # Returns: {"contracts": 1, "stop_loss": 4498.0, "take_profit": 4506.0}


# ── BUY handler (async so it can await the C++ subprocess) ───────────────────
async def handle_buy_signal(ib: IB, mes, close_price: float):
    global in_trade, daily_pnl, entry_price

    if daily_pnl <= -MAX_DAILY_LOSS:
        log.info("Circuit breaker active — no new trades today")
        return

    risk      = await call_cpp_risk(close_price)
    contracts = risk["contracts"]
    sl_price  = risk["stop_loss"]
    tp_price  = risk["take_profit"]

    net_win  = (tp_price - close_price) * POINT_VALUE - COMMISSION * 2
    net_loss = (close_price - sl_price) * POINT_VALUE + COMMISSION * 2
    log.info(
        f"ENTRY  price={close_price:.2f}  SL={sl_price:.2f}  TP={tp_price:.2f}  "
        f"net_win=${net_win:.2f}  net_loss=${net_loss:.2f}  "
        f"effective_RR={net_win/net_loss:.2f}"
    )

    # Bracket order: entry → stop loss + take profit (OCA group)
    entry_order = Order(action="BUY", totalQuantity=contracts,
                        orderType="MKT", transmit=False)
    entry_trade = ib.placeOrder(mes, entry_order)

    sl_order = StopOrder("SELL", contracts, sl_price,
                          parentId=entry_trade.order.orderId, transmit=False)
    ib.placeOrder(mes, sl_order)

    tp_order = LimitOrder("SELL", contracts, tp_price,
                           parentId=entry_trade.order.orderId,
                           ocaGroup=f"MES_OCA_{id(entry_trade)}",
                           transmit=True)
    ib.placeOrder(mes, tp_order)

    in_trade    = True
    entry_price = close_price


async def run_bot():
    global in_trade, daily_pnl

    ib = IB()
    await ib.connectAsync(CFG["ib_host"], CFG["ib_port"], clientId=CFG["ib_client_id"])
    log.info("Connected to IB Gateway (paper trading)")

    contract = Future(symbol=CFG["symbol"], exchange=CFG["exchange"], currency=CFG["currency"])
    quals = await ib.qualifyContractsAsync(contract)
    mes = quals[0]
    log.info(f"Contract: {mes.localSymbol}  expiry={mes.lastTradeDateOrContractMonth}")

    # IB only streams 5-sec real-time bars; aggregate 12 of them into 1-min bars
    bars: BarDataList = ib.reqRealTimeBars(mes, barSize=5, whatToShow="TRADES", useRTH=False)
    bar_count = 0

    def on_bar_update(bars: BarDataList, has_new_bar: bool):
        # Sync callback — cannot await here. Schedule async work via
        # asyncio.ensure_future() so the event loop handles it next tick.
        nonlocal bar_count
        if not has_new_bar:
            return
        bar_count += 1
        if bar_count % 12 != 0:   # 12 × 5-sec = 1 complete 1-min bar
            return

        latest = bars[-1]
        closes.append(latest.close)

        if not is_market_hours():
            return

        signal = check_signal()
        log.info(
            f"1-min  close={latest.close:.2f}  "
            f"MA{MA_FAST}={ma(closes, MA_FAST):.2f}  "
            f"MA{MA_SLOW}={ma(closes, MA_SLOW):.2f}  "
            f"signal={signal}"
        )

        if signal == "BUY" and not in_trade:
            asyncio.ensure_future(handle_buy_signal(ib, mes, latest.close))

        elif signal == "SELL" and in_trade:
            log.info("MA crossover SELL — closing position")
            close_order = Order(action="SELL", totalQuantity=MAX_CONTRACTS,
                                orderType="MKT", transmit=True)
            ib.placeOrder(mes, close_order)
            in_trade = False

    bars.updateEvent += on_bar_update

    log.info("Streaming MES bars. Press Ctrl-C to stop.")
    try:
        while ib.isConnected():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        ib.cancelRealTimeBars(bars)
        ib.disconnect()


if __name__ == "__main__":
    asyncio.run(run_bot())
