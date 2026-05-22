"""
One-off script: reattach chunk text to eval result files that were generated
before harness.py was fixed to save the text field.

For each record, re-runs embed+hybrid_retrieve on the original question
(deterministic), verifies source_file order matches, then fills in chunk text.
"""
import sys
import io
import json
import os
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import dotenv
dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True))

from openai import OpenAI
from pinecone import Pinecone
from production_rag_forensics.retrieval.hybrid_search import get_hybrid_search

_oai   = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
_index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index("fastapi-docs-v1")
_hs    = get_hybrid_search(_index)


def retrieve_chunks(question: str) -> list[dict]:
    vec = _oai.embeddings.create(
        input=[question], model="text-embedding-3-small"
    ).data[0].embedding
    return _hs.search(query=question, query_embedding=vec, top_k=5, dense_n=20, sparse_n=20)


mismatches = 0
for prov in ["anthropic", "openai"]:
    path = Path(f"data/eval_results_xprov_{prov}.jsonl")
    records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"\n{prov}: reattaching chunk text for {len(records)} records...")

    for i, rec in enumerate(records):
        live_chunks = retrieve_chunks(rec["question"])
        saved_sources = [c["source_file"] for c in rec["chunks"]]
        live_sources  = [c["source_file"] for c in live_chunks[:len(saved_sources)]]
        if saved_sources != live_sources:
            mismatches += 1
            print(f"  MISMATCH {rec['id']}: saved={saved_sources}")
            print(f"            live ={live_sources}")
        for j, chunk in enumerate(rec["chunks"]):
            if j < len(live_chunks):
                chunk["text"] = live_chunks[j].get("text", "")
        if (i + 1) % 30 == 0:
            print(f"  {i+1}/{len(records)} done")

    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    sample = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    text_len = len(sample["chunks"][0].get("text", ""))
    print(f"  Written. Sample chunk 0 text: {text_len} chars — {'OK' if text_len > 0 else 'EMPTY'}")

print(f"\nTotal source_file mismatches: {mismatches}")
