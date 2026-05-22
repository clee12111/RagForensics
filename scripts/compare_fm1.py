import json

# Original dense baseline (no few-shot prompt)
orig = {json.loads(l)['id']: json.loads(l) for l in open('data/eval_results.jsonl')}
# Hybrid + few-shot prompt (this run)
hybrid = {json.loads(l)['id']: json.loads(l) for l in open('data/eval_results_hybrid_fm1.jsonl')}

fm1_ids = ['c3_12','c3_20','c3_24','c4_29','c5_11']

print(f"{'ID':<8} {'orig dense':>10} {'hybrid+fewshot':>15} {'delta':>7}")
print('-'*45)
for qid in fm1_ids:
    o = orig[qid]['faithfulness']
    h = hybrid[qid]['faithfulness']
    print(f"{qid:<8} {o:>10} {h:>15} {h-o:>+7}")
print(f"\nFM-1 chunks retrieved (hybrid):")
for qid in fm1_ids:
    chunks = hybrid[qid]['chunks']
    print(f"  {qid}: {[c['source_file'] for c in chunks]}")
