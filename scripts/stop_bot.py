"""
Stop the running MES bot by PID file.
Usage: py -3.11 scripts/stop_bot.py
"""
import os
import pathlib
import signal
import sys

PID_FILE = pathlib.Path("data/mesbot.pid")

if not PID_FILE.exists():
    print("Bot is not running (no PID file found).")
    sys.exit(0)

pid = int(PID_FILE.read_text().strip())

try:
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)
        if not handle:
            print(f"No process found with PID {pid} — already stopped.")
            PID_FILE.unlink(missing_ok=True)
            sys.exit(0)
        ctypes.windll.kernel32.TerminateProcess(handle, 0)
        ctypes.windll.kernel32.CloseHandle(handle)
    else:
        os.kill(pid, signal.SIGTERM)
    print(f"MES bot (PID {pid}) stopped.")
    PID_FILE.unlink(missing_ok=True)
except ProcessLookupError:
    print(f"No process found with PID {pid} — already stopped.")
    PID_FILE.unlink(missing_ok=True)
