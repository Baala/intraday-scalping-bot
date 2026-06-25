import csv
from collections import defaultdict

with open("data/mes_15min.csv") as f:
    rows = list(csv.DictReader(f))

months = defaultdict(list)
for r in rows:
    months[r["date"][:7]].append(r)

print("Bars per month (expected ~546 for a full month):")
for m in sorted(months):
    n = len(months[m])
    quality = "OK" if n > 400 else ("PARTIAL" if n > 50 else "SPARSE — likely daily bars")
    print(f"  {m}: {n:>4} bars  {quality}")

print()
print("Sample rows from 2025-08 (sparse month):")
for r in months.get("2025-08", [])[:3]:
    print(f"  {r['date']}  O={r['open']}  H={r['high']}  L={r['low']}  C={r['close']}  V={r['volume']}")

print()
print("Sample rows from 2026-04 (good month):")
for r in months.get("2026-04", [])[:3]:
    print(f"  {r['date']}  O={r['open']}  H={r['high']}  L={r['low']}  C={r['close']}  V={r['volume']}")
