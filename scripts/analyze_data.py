import csv
import math
from collections import defaultdict

with open("data/mes_15min.csv") as f:
    rows = list(csv.DictReader(f))

print(f"Total bars : {len(rows)}")
print(f"Date range : {rows[0]['date']} to {rows[-1]['date']}")

# ── Monthly summary ────────────────────────────────────────────────────────────
months = defaultdict(list)
for r in rows:
    months[r["date"][:7]].append(r)

print("\nMonthly breakdown:")
print(f"  {'Month':<10} {'Range%':>7}  {'Net%':>7}  {'Dir':>4}  {'Bars':>5}  {'Verdict'}")
print(f"  {'-'*10} {'-'*7}  {'-'*7}  {'-'*4}  {'-'*5}  {'-'*20}")

for m in sorted(months):
    closes = [float(r["close"]) for r in months[m]]
    rng    = (max(closes) - min(closes)) / closes[0] * 100
    net    = (closes[-1] - closes[0])   / closes[0] * 100
    dirn   = "UP" if closes[-1] > closes[0] else "DOWN"
    # Choppiness: small net move relative to range = choppy
    chop   = abs(net) / rng if rng > 0 else 0
    verdict = "TRENDING" if chop > 0.35 else "CHOPPY"
    print(f"  {m:<10} {rng:>6.1f}%  {net:>+6.1f}%  {dirn:>4}  {len(months[m]):>5}  {verdict}")

# ── ADX approximation (14-period EMA) ────────────────────────────────────────
print("\nSimple trend strength — 14-bar ADX estimate by month:")
ADX_MULT = 2.0 / 15
prev_h = prev_l = prev_c = None
dm_p = dm_m = tr_e = dx_e = 0.0
adx_by_month = defaultdict(list)

for r in rows:
    h, l, c = float(r["high"]), float(r["low"]), float(r["close"])
    m = r["date"][:7]
    if prev_c is None:
        prev_h, prev_l, prev_c = h, l, c
        continue
    up   = h - prev_h
    down = prev_l - l
    dmp  = up   if (up > down and up > 0)   else 0.0
    dmm  = down if (down > up and down > 0) else 0.0
    tr   = max(h - l, abs(h - prev_c), abs(l - prev_c))
    prev_h, prev_l, prev_c = h, l, c
    dm_p = dm_p + ADX_MULT * (dmp  - dm_p)
    dm_m = dm_m + ADX_MULT * (dmm  - dm_m)
    tr_e = tr_e + ADX_MULT * (tr   - tr_e)
    if tr_e == 0:
        continue
    di_p = 100 * dm_p / tr_e
    di_m = 100 * dm_m / tr_e
    di_s = di_p + di_m
    dx   = 100 * abs(di_p - di_m) / di_s if di_s > 0 else 0.0
    dx_e = dx_e + ADX_MULT * (dx - dx_e)
    adx_by_month[m].append(dx_e)

print(f"  {'Month':<10} {'Avg ADX':>8}  {'% bars < 20':>12}  {'Verdict'}")
print(f"  {'-'*10} {'-'*8}  {'-'*12}  {'-'*15}")
for m in sorted(adx_by_month):
    vals    = adx_by_month[m]
    avg_adx = sum(vals) / len(vals)
    pct_low = sum(1 for v in vals if v < 20) / len(vals) * 100
    verdict = "CHOPPY" if pct_low > 50 else "TRENDING"
    print(f"  {m:<10} {avg_adx:>8.1f}  {pct_low:>11.0f}%  {verdict}")
