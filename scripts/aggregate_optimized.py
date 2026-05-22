import json
from collections import defaultdict
from statistics import mean

def load(path):
    return [json.loads(l) for l in open(path)]

baseline = load('data/eval_results.jsonl')  # re-scored, has faith + p@5
runs = [
    load('data/eval_results_optimized_run1.jsonl'),
    load('data/eval_results_optimized_run2.jsonl'),
    load('data/eval_results_optimized_run3.jsonl'),
]

CATS = ['conceptual','syntactic','cross_reference','edge_case','out_of_scope']

def cat_means(records, metric):
    d = defaultdict(list)
    for r in records:
        v = r.get(metric)
        if v is not None:
            d[r['category']].append(v)
    return {c: mean(d[c]) for c in d}

# Baseline
base_faith = cat_means(baseline, 'faithfulness')
base_p5    = cat_means(baseline, 'precision_at_5')

# Each run's per-category means
run_faith = [cat_means(r, 'faithfulness') for r in runs]
run_p5    = [cat_means(r, 'precision_at_5') for r in runs]

print("="*72)
print("PER-RUN FAITHFULNESS (3 optimized runs)")
print("="*72)
print(f"{'Category':<18}{'Run1':>8}{'Run2':>8}{'Run3':>8}{'Mean':>8}{'Spread':>8}")
print("-"*72)
for c in CATS:
    vals = [rf[c] for rf in run_faith]
    print(f"{c:<18}{vals[0]:>8.2f}{vals[1]:>8.2f}{vals[2]:>8.2f}{mean(vals):>8.2f}{max(vals)-min(vals):>8.2f}")

print("\n"+"="*72)
print("PER-RUN PRECISION@5 (3 optimized runs)")
print("="*72)
print(f"{'Category':<18}{'Run1':>8}{'Run2':>8}{'Run3':>8}{'Mean':>8}{'Spread':>8}")
print("-"*72)
for c in CATS:
    vals = [rp[c] for rp in run_p5]
    print(f"{c:<18}{vals[0]:>8.2f}{vals[1]:>8.2f}{vals[2]:>8.2f}{mean(vals):>8.2f}{max(vals)-min(vals):>8.2f}")

print("\n"+"="*72)
print("BASELINE vs OPTIMIZED (mean of 3 runs)  —  FAITHFULNESS")
print("="*72)
print(f"{'Category':<18}{'Baseline':>10}{'Optimized':>11}{'Delta':>8}{'Spread':>8}{'Real?':>7}")
print("-"*72)
for c in CATS:
    opt_vals = [rf[c] for rf in run_faith]
    opt = mean(opt_vals); spread = max(opt_vals)-min(opt_vals)
    delta = opt - base_faith[c]
    real = "yes" if abs(delta) > spread else "noise"
    print(f"{c:<18}{base_faith[c]:>10.2f}{opt:>11.2f}{delta:>+8.2f}{spread:>8.2f}{real:>7}")
# totals
all_base = [r['faithfulness'] for r in baseline if r.get('faithfulness') is not None]
all_opt  = [mean([rf[c] for rf in run_faith]) for c in CATS]
print("-"*72)
print(f"{'TOTAL':<18}{mean(all_base):>10.2f}{mean(all_opt):>11.2f}{mean(all_opt)-mean(all_base):>+8.2f}")

print("\n"+"="*72)
print("BASELINE vs OPTIMIZED (mean of 3 runs)  —  PRECISION@5")
print("="*72)
print(f"{'Category':<18}{'Baseline':>10}{'Optimized':>11}{'Delta':>8}{'Spread':>8}{'Real?':>7}")
print("-"*72)
for c in CATS:
    opt_vals = [rp[c] for rp in run_p5]
    opt = mean(opt_vals); spread = max(opt_vals)-min(opt_vals)
    delta = opt - base_p5[c]
    real = "yes" if abs(delta) > spread else "noise"
    print(f"{c:<18}{base_p5[c]:>10.2f}{opt:>11.2f}{delta:>+8.2f}{spread:>8.2f}{real:>7}")

# faith=0 and flag counts across runs
print("\n"+"="*72)
print("FAITH=0 and FLAGGED counts")
print("="*72)
b0 = sum(1 for r in baseline if r.get('faithfulness')==0)
print(f"Baseline: faith=0 count = {b0}")
for i, r in enumerate(runs, 1):
    z = sum(1 for x in r if x.get('faithfulness')==0)
    f = sum(1 for x in r if x.get('judge_provider')=='flagged_ambiguous')
    print(f"Optimized run{i}: faith=0 = {z}, flagged = {f}")
