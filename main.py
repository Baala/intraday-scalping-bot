"""
Entry point: python main.py --mode paper | live
Runs bot + dashboard server in a single asyncio event loop.
"""
import argparse
import asyncio
import logging
import pathlib
import sys

import uvicorn

from bot.scalper import run_bot
from dashboard.server import app

pathlib.Path("data").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/bot.log", encoding="utf-8"),
    ],
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MES 15-min scalping bot")
    p.add_argument("--mode", choices=["paper", "live"], default="paper",
                   help="Trading mode (default: paper)")
    return p.parse_args()


async def main(mode: str) -> None:
    import json
    with open("config/scalping_config.json") as f:
        cfg = json.load(f)

    server_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=cfg["dashboard_port"],
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    print(f"[MES Bot] mode={mode.upper()}  dashboard=http://localhost:{cfg['dashboard_port']}")

    await asyncio.gather(
        asyncio.create_task(run_bot(mode)),
        asyncio.create_task(server.serve()),
        return_exceptions=True,
    )


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(main(args.mode))
    except KeyboardInterrupt:
        print("\n[MES Bot] Stopped.")
