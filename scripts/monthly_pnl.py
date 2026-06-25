import csv
from collections import defaultdict

with open("data/backtest_results.csv") as f:
    trades = list(csv.DictReader(f))

print(f"Total trades in log: {len(trades)}")
print()

monthly = defaultdict(list)
for t in trades:
    month = t["entry_time"][:7]
    monthly[month].append(float(t["pnl"]))

print(f"{'Month':<10} {'Trades':>6}  {'Wins':>4}  {'Losses':>6}  {'WR%':>6}  {'P&L':>9}  {'Cumulative':>11}")
print("-" * 65)
cumulative = 0.0
for m in sorted(monthly):
    pnls   = monthly[m]
    wins   = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    total  = sum(pnls)
    wr     = wins / len(pnls) * 100 if pnls else 0
    cumulative += total
    flag = " <<< PROFIT" if total > 0 else ""
    print(f"{m:<10} {len(pnls):>6}  {wins:>4}  {losses:>6}  {wr:>5.0f}%  ${total:>8.2f}  ${cumulative:>10.2f}{flag}")

print()
print(f"Avg trades/month : {len(trades)/len(monthly):.1f}")
print(f"Profitable months: {sum(1 for v in monthly.values() if sum(v) > 0)} / {len(monthly)}")
