"""
Slippage capital-drag report for scalping_performance_*.csv

Run:
    py -3.11 scripts/slippage_report.py [--mode paper|live]

Columns required (present from v3 CSV schema):
    signal_price, entry_price, exit_price, direction, sl_points,
    contracts, exit_reason, is_atr_capped, signal_type, true_atr_at_entry
"""
import argparse
import pathlib
import sys

try:
    import pandas as pd
except ImportError:
    sys.exit("pandas not installed — run: py -3.11 -m pip install pandas")


def load(mode: str) -> pd.DataFrame:
    path = pathlib.Path(f"data/scalping_performance_{mode}.csv")
    if not path.exists():
        sys.exit(f"No CSV at {path}")
    df = pd.read_csv(path)
    required = {"signal_price", "entry_price", "exit_price", "direction",
                "sl_points", "contracts", "exit_reason"}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"CSV missing columns (pre-v3 schema?): {missing}")
    return df


def compute_slippage(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["d"] = df["direction"].map({"LONG": 1, "SHORT": -1})

    # Entry slippage — positive = alpha loss (paid above close for LONG, sold below for SHORT)
    df["entry_slip_pts"] = df["d"] * (df["entry_price"] - df["signal_price"])
    df["entry_slip_usd"] = df["entry_slip_pts"] * 5.0 * df["contracts"]

    # Theoretical bracket levels (from filled entry price)
    df["theoretical_sl"] = df["entry_price"] - df["d"] * df["sl_points"]
    df["theoretical_tp"] = df["entry_price"] + df["d"] * df["sl_points"] * 2.0

    # SL exit slippage — positive = filled worse than stop (gap-through)
    sl_mask = df["exit_reason"] == "SL"
    df.loc[sl_mask, "sl_exit_slip_pts"] = (
        df.loc[sl_mask, "d"] *
        (df.loc[sl_mask, "theoretical_sl"] - df.loc[sl_mask, "exit_price"])
    )
    df.loc[sl_mask, "sl_exit_slip_usd"] = (
        df.loc[sl_mask, "sl_exit_slip_pts"] * 5.0 * df.loc[sl_mask, "contracts"]
    )

    # TP exit slippage — positive = price improvement (limit filled better than target)
    tp_mask = df["exit_reason"] == "TP"
    df.loc[tp_mask, "tp_exit_slip_pts"] = (
        df.loc[tp_mask, "d"] *
        (df.loc[tp_mask, "exit_price"] - df.loc[tp_mask, "theoretical_tp"])
    )
    df.loc[tp_mask, "tp_exit_slip_usd"] = (
        df.loc[tp_mask, "tp_exit_slip_pts"] * 5.0 * df.loc[tp_mask, "contracts"]
    )

    return df


def print_section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


def run(mode: str) -> None:
    raw = load(mode)
    df  = compute_slippage(raw)

    total        = len(df)
    signal_exits = df[df["exit_reason"] != "EOD_FLUSH"]
    sl_exits     = df[df["exit_reason"] == "SL"]
    tp_exits     = df[df["exit_reason"] == "TP"]
    eod_exits    = df[df["exit_reason"] == "EOD_FLUSH"]

    print_section("Exit Distribution")
    print(df["exit_reason"].value_counts().to_string())
    print(f"\nSignal win rate (excl EOD_FLUSH): "
          f"{(signal_exits['exit_reason'] == 'TP').mean():.1%}  "
          f"n={len(signal_exits)}")

    print_section("Entry Slippage  [positive = alpha loss]")
    s = df["entry_slip_pts"]
    print(f"  Mean : {s.mean():+.4f} pts  (${df['entry_slip_usd'].mean():+.2f})")
    print(f"  Worst: {s.max():+.4f} pts  (${df['entry_slip_usd'].max():+.2f})")
    print(f"  Total drag: ${df['entry_slip_usd'].sum():.2f}")

    if len(sl_exits):
        print_section("SL Exit Slippage  [positive = gap-through loss]")
        s = sl_exits["sl_exit_slip_pts"].dropna()
        u = sl_exits["sl_exit_slip_usd"].dropna()
        print(f"  Mean : {s.mean():+.4f} pts  (${u.mean():+.2f})")
        print(f"  Worst: {s.max():+.4f} pts  (${u.max():+.2f})")
        print(f"  Total drag: ${u.sum():.2f}")

    if len(tp_exits):
        print_section("TP Exit Slippage  [positive = price improvement / alpha gain]")
        s = tp_exits["tp_exit_slip_pts"].dropna()
        u = tp_exits["tp_exit_slip_usd"].dropna()
        print(f"  Mean : {s.mean():+.4f} pts  (${u.mean():+.2f})")
        print(f"  Best : {s.max():+.4f} pts  (${u.max():+.2f})")
        print(f"  Total gain: ${u.sum():.2f}")

    # Total capital drag = entry slip + SL slip (TP slip is a gain, not cost)
    total_entry_drag = df["entry_slip_usd"].sum()
    total_sl_drag    = sl_exits["sl_exit_slip_usd"].dropna().sum()
    total_tp_gain    = tp_exits["tp_exit_slip_usd"].dropna().sum()
    net_drag         = total_entry_drag + total_sl_drag - total_tp_gain

    print_section("Net Capital Drag")
    print(f"  Entry slippage drag : ${total_entry_drag:+.2f}")
    print(f"  SL gap-through drag : ${total_sl_drag:+.2f}")
    print(f"  TP improvement gain : ${total_tp_gain:+.2f}")
    print(f"  Net drag            : ${net_drag:+.2f}  "
          f"({net_drag / max(1, total):.2f}/trade avg)")

    # ATR cap regime breakdown
    if "is_atr_capped" in df.columns and df["is_atr_capped"].notna().any():
        print_section("ATR Cap Regime  (0 = normal, 1 = soft-capped)")
        print(df.groupby("is_atr_capped")["exit_reason"]
              .value_counts(normalize=True)
              .unstack(fill_value=0)
              .reindex(columns=["TP", "SL", "EOD_FLUSH"], fill_value=0)
              .rename(index={0: "normal", 1: "capped"})
              .applymap(lambda x: f"{x:.1%}"))

        print("\nEntry slippage by ATR regime:")
        print(df.groupby("is_atr_capped")["entry_slip_pts"]
              .agg(["mean", "max", "count"])
              .rename(index={0: "normal", 1: "capped"})
              .round(4))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="paper", choices=["paper", "live"])
    args = ap.parse_args()
    run(args.mode)
