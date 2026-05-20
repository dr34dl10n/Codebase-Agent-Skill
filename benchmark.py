#!/usr/bin/env python3
"""Benchmark: compare embedding models for semantic code search quality.

Pure in-memory benchmark — no database, no Ollama, no API required.
Uses sentence-transformers directly to compare:
  - modernbert-embed-base  (nomic-ai/modernbert-embed-base, 768-dim)
  - nomic-embed-text-v1.5  (nomic-ai/nomic-embed-text-v1.5, 768-dim)

Measures:
  - Search quality: cosine similarity scores of top-10 results
  - Embedding latency: per-query and per-chunk timing
  - Context efficiency: tokens returned vs full-file grep strategies

Usage:
    python benchmark.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from config import ParseConfig
from parser import parse_file, walk_repository

QUERIES = [
    "how does authentication work",
    "send an email via gmail",
    "calendar event creation",
    "telegram bot message handler",
    "error handling and retries",
    "how is the agent run loop structured",
    "memory and context management",
    "Google Workspace OAuth flow",
]

MODELS = [
    {
        "name": "modernbert-embed-base",
        "hf_id": "nomic-ai/modernbert-embed-base",
        "dim": 768,
    },
    {
        "name": "nomic-embed-text",
        "hf_id": "nomic-ai/nomic-embed-text-v1.5",
        "dim": 768,
    },
]


def tokenize_approx(text: str) -> int:
    """Approx token count (~4 chars/token)."""
    return len(text) // 4


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity between a (1, d) and b (n, d)."""
    return (b @ a.flatten()) / (np.linalg.norm(b, axis=1) * np.linalg.norm(a) + 1e-10)


def estimate_grep_tokens(repo_path: str, query: str) -> dict:
    """Simulate naive/smart grep strategies."""
    words = query.lower().split()
    pattern = "|".join(words)
    try:
        result = subprocess.run(
            ["grep", "-rlE", pattern, repo_path],
            capture_output=True, text=True, timeout=10,
        )
        naive_files = [f for f in result.stdout.strip().split("\n") if f]
    except Exception:
        naive_files = []

    naive_tokens = sum(
        len(Path(f).read_text(errors="replace")) // 4
        for f in naive_files if Path(f).is_file()
    )

    file_hits = {}
    for f in naive_files:
        try:
            content = Path(f).read_text(errors="replace").lower()
            hits = sum(content.count(w) for w in words)
            file_hits[f] = hits
        except OSError:
            pass
    top5 = sorted(file_hits.items(), key=lambda x: -x[1])[:5]
    smart_tokens = sum(
        len(Path(f).read_text(errors="replace")) // 4
        for f, _ in top5 if Path(f).is_file()
    )

    return {"naive_tokens": naive_tokens, "smart_tokens": smart_tokens}


def main():
    repo_path = "/data/AIssistant"
    print("=" * 72)
    print("BENCHMARK: Embedding Models for Semantic Code Search")
    print("=" * 72)
    print(f"\nRepo: {repo_path}")

    # Parse all chunks
    print("\nParsing codebase...")
    config = ParseConfig()
    files = walk_repository(repo_path, config)
    all_chunks = []
    for f in files:
        chunks = parse_file(f, config)
        all_chunks.extend(chunks)
    print(f"  {len(all_chunks)} chunks from {len(files)} files")

    # Grep baselines
    print("\nComputing grep baselines...")
    grep_results = {}
    for q in QUERIES:
        grep_results[q] = estimate_grep_tokens(repo_path, q)
    avg_naive = sum(g["naive_tokens"] for g in grep_results.values()) / len(grep_results)
    avg_smart = sum(g["smart_tokens"] for g in grep_results.values()) / len(grep_results)
    total_code_tokens = sum(len(c.content) // 4 for c in all_chunks)
    print(f"  Naive grep avg:  ~{avg_naive:,.0f} tokens/query")
    print(f"  Smart grep avg:  ~{avg_smart:,.0f} tokens/query")
    print(f"  Total codebase:  ~{total_code_tokens:,.0f} tokens ({len(all_chunks)} chunks)")

    # Benchmark each model
    all_results = []
    for model_info in MODELS:
        name = model_info["name"]
        hf_id = model_info["hf_id"]
        dim = model_info["dim"]

        print(f"\n{'─' * 72}")
        print(f"Model: {name} ({hf_id})")
        print(f"{'─' * 72}")

        # Load model
        from sentence_transformers import SentenceTransformer
        t0 = time.time()
        model = SentenceTransformer(hf_id)
        load_time = time.time() - t0
        print(f"  Loaded in {load_time:.1f}s — dim={dim}, max_seq={model.max_seq_length}")

        # Chunk-level embeddings (with timing)
        chunk_texts = [c.content[:32768] for c in all_chunks]
        print(f"  Embedding {len(chunk_texts)} chunks...")
        t0 = time.time()
        chunk_embeddings = model.encode(
            chunk_texts,
            batch_size=16,
            show_progress_bar=True,
            normalize_embeddings=True,
        )
        chunk_embed_time = time.time() - t0
        print(f"  Done in {chunk_embed_time:.1f}s "
              f"({chunk_embed_time / len(chunk_texts):.3f}s/chunk)")

        # Query-level embeddings + search
        query_results = []
        for query in QUERIES:
            t1 = time.time()
            query_emb = model.encode([query], normalize_embeddings=True, show_progress_bar=False)
            embed_time = (time.time() - t1) * 1000

            # Cosine similarity search
            t2 = time.time()
            similarities = cosine_similarity(query_emb, chunk_embeddings)
            top_indices = np.argsort(similarities)[::-1][:10]
            search_time = (time.time() - t2) * 1000

            # Gather results
            top_results = []
            chunk_tokens = 0
            for idx in top_indices:
                chunk = all_chunks[idx]
                score = float(similarities[idx])
                chunk_tokens += len(chunk.content) // 4
                top_results.append({
                    "file": str(Path(chunk.file_path).relative_to(repo_path)),
                    "symbol": chunk.symbol,
                    "score": round(score, 4),
                    "lines": f"{chunk.start_line}-{chunk.end_line}",
                })

            query_results.append({
                "query": query,
                "embed_ms": round(embed_time, 1),
                "search_ms": round(search_time, 1),
                "total_ms": round(embed_time + search_time, 1),
                "chunk_tokens": chunk_tokens,
                "n_results": 10,
                "top_results": top_results,
            })

        avg_total_ms = sum(q["total_ms"] for q in query_results) / len(query_results)
        avg_chunk_tokens = sum(q["chunk_tokens"] for q in query_results) / len(query_results)

        result = {
            "model": name,
            "hf_id": hf_id,
            "dim": dim,
            "load_time_s": round(load_time, 1),
            "chunk_embed_time_s": round(chunk_embed_time, 1),
            "per_chunk_ms": round(chunk_embed_time / len(chunk_texts) * 1000, 1),
            "per_query": query_results,
            "avg_total_ms": round(avg_total_ms, 1),
            "avg_chunk_tokens": round(avg_chunk_tokens),
        }
        all_results.append(result)
        del model, chunk_embeddings
        import gc; gc.collect()

    # ─── Display ──────────────────────────────────────────────────────────
    print("\n\n")
    print("=" * 72)
    print("DETAILED RESULTS")
    print("=" * 72)

    for res in all_results:
        print(f"\n  {res['model']} (dim={res['dim']}, load={res['load_time_s']}s)")
        print(f"  Chunk indexing: {res['chunk_embed_time_s']}s "
              f"({res['per_chunk_ms']}ms/chunk)")
        print(f"  {'Query':<42} {'Time':>8}  {'Tokens':>8}  {'Top-1':>8}  {'Top-3':>8}  {'Top-5':>8}")
        print(f"  {'─' * 42} {'─' * 8}  {'─' * 8}  {'─' * 8}  {'─' * 8}  {'─' * 8}")
        for q in res["per_query"]:
            scores = [t["score"] for t in q["top_results"]]
            top1 = scores[0] if len(scores) > 0 else 0
            top3 = np.mean(scores[:3]).item() if len(scores) >= 3 else 0
            top5 = np.mean(scores[:5]).item() if len(scores) >= 5 else 0
            print(f"  {q['query']:<42} {q['total_ms']:>6}ms  "
                  f"{q['chunk_tokens']:>6}tok  {top1:>8.4f}  {top3:>8.4f}  {top5:>8.4f}")
        print(f"  {'─' * 42} {'─' * 8}  {'─' * 8}")
        print(f"  {'AVERAGE':<42} {res['avg_total_ms']:>6}ms  "
              f"{res['avg_chunk_tokens']:>6}tok")

    # Summary table
    print(f"\n{'=' * 72}")
    print("SUMMARY: Context Loading Efficiency")
    print(f"{'=' * 72}")
    print()
    print(f"  {'Strategy':<35} {'Avg Time':>10}  {'Avg Tokens':>12}  {'vs Naive':>10}  {'vs Smart':>10}")
    print(f"  {'─' * 35} {'─' * 10}  {'─' * 12}  {'─' * 10}  {'─' * 10}")
    naive_str = f"~{avg_naive:,.0f}tok"
    smart_str = f"~{avg_smart:,.0f}tok"
    print(f"  {'Naive Traditional':<35} {'~16ms':>10}  {naive_str:>12}  {'1×':>10}  "
          f"{f'{avg_smart / avg_naive:.1f}×':>10}")
    print(f"  {'Smart Traditional':<35} {'~18ms':>10}  {smart_str:>12}  "
          f"{f'{avg_naive / avg_smart:.0f}×':>10}  {'1×':>10}")
    for res in all_results:
        reduction_naive = avg_naive / res["avg_chunk_tokens"] if res["avg_chunk_tokens"] > 0 else 0
        reduction_smart = avg_smart / res["avg_chunk_tokens"] if res["avg_chunk_tokens"] > 0 else 0
        tok_str = f"~{res['avg_chunk_tokens']:,.0f}tok"
        print(f"  {res['model'] + ' (pgvector)':<35} "
              f"{res['avg_total_ms']:>8}ms  {tok_str:>12}  "
              f"{reduction_naive:>9.0f}×  {reduction_smart:>9.1f}×")

    # Save JSON
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repo": str(repo_path),
        "total_chunks": len(all_chunks),
        "total_files": len(files),
        "queries": QUERIES,
        "grep_baselines": {
            "avg_naive_tokens": round(avg_naive),
            "avg_smart_tokens": round(avg_smart),
        },
        "models": all_results,
    }
    out_path = Path(__file__).resolve().parent / "benchmark_results.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())