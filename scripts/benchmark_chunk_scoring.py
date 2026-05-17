#!/usr/bin/env python3
"""Anonymized benchmark for raw vs mean-centered chunk scoring.

The report intentionally does not include note paths, headings, titles, or
chunk text. It is meant to make anisotropy visible through aggregate metrics.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_QUERIES = [
    ("anisotropy_cosine_bias", "анизотропия эмбеддингов косинусное сходство все похоже на все"),
    ("agent_memory_structure", "структурная память для Obsidian и AI агентов"),
    ("chunk_retrieval", "поиск по чанкам retrieval точные фрагменты вместо целых файлов"),
    ("semantic_bridges", "семантические мосты теги граф знаний заметки"),
    ("complex_systems", "сложные системы резонанс когерентность паттерны"),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run an anonymized raw-vs-centered benchmark for NOUZ chunk embeddings."
    )
    parser.add_argument("--db", required=True, help="Path to obsidian_kb.db")
    parser.add_argument("--out", required=True, help="Directory for summary.json and summary.md")
    parser.add_argument("--endpoint", default="http://localhost:1234/v1/embeddings")
    parser.add_argument("--model", default="text-embedding-granite-embedding-278m-multilingual")
    parser.add_argument("--pairs", type=int, default=20000, help="Sampled chunk pairs for corpus diagnostics")
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()

    started = time.perf_counter()
    db_path = Path(args.db)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_chunk_vectors(db_path)
    if not rows:
        raise SystemExit(f"No chunk embeddings found in {db_path}")

    centroid = vector_centroid([row["embedding"] for row in rows])
    corpus = corpus_diagnostics(rows, centroid, sample_pairs=args.pairs, seed=args.seed)

    queries = []
    for label, query_text in DEFAULT_QUERIES:
        query_vec = fetch_embedding(args.endpoint, args.model, query_text)
        queries.append(benchmark_query(label, query_vec, rows, centroid))

    elapsed_sec = round(time.perf_counter() - started, 3)
    report = {
        "privacy": {
            "contains_note_paths": False,
            "contains_note_titles": False,
            "contains_note_headings": False,
            "contains_note_text": False,
            "query_texts_omitted": True,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db": {
            "chunk_count": len(rows),
            "file_count": len({row["path"] for row in rows}),
            "embedding_dim": len(rows[0]["embedding"]),
        },
        "embedding_model": args.model,
        "corpus": corpus,
        "queries": queries,
        "elapsed_sec": elapsed_sec,
    }

    (out_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote anonymized benchmark to {out_dir}")
    return 0


def load_chunk_vectors(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        records = con.execute(
            "select chunk_id, path, embedding from chunk_embeddings where embedding is not null"
        ).fetchall()
    finally:
        con.close()

    rows = []
    expected_dim = None
    for record in records:
        try:
            vector = json.loads(record["embedding"])
        except Exception:
            continue
        if not isinstance(vector, list) or not vector:
            continue
        if expected_dim is None:
            expected_dim = len(vector)
        if len(vector) != expected_dim:
            continue
        rows.append(
            {
                "chunk_id": str(record["chunk_id"]),
                "path": str(record["path"]),
                "embedding": [float(value) for value in vector],
            }
        )
    return rows


def fetch_embedding(endpoint: str, model: str, text: str) -> list[float]:
    payload = json.dumps({"model": model, "input": text}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
    return [float(value) for value in data["data"][0]["embedding"]]


def corpus_diagnostics(
    rows: list[dict[str, Any]],
    centroid: list[float],
    *,
    sample_pairs: int,
    seed: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    raw_values = []
    centered_values = []
    same_file_raw = []
    same_file_centered = []
    cross_file_raw = []
    cross_file_centered = []

    centered_cache = {
        row["chunk_id"]: subtract_vector(row["embedding"], centroid)
        for row in rows
    }

    for _ in range(sample_pairs):
        left, right = rng.sample(rows, 2)
        raw = cosine(left["embedding"], right["embedding"])
        centered = cosine(centered_cache[left["chunk_id"]], centered_cache[right["chunk_id"]])
        raw_values.append(raw)
        centered_values.append(centered)

        if left["path"] == right["path"]:
            same_file_raw.append(raw)
            same_file_centered.append(centered)
        else:
            cross_file_raw.append(raw)
            cross_file_centered.append(centered)

    same_raw = median_or_none(same_file_raw)
    cross_raw = median_or_none(cross_file_raw)
    same_centered = median_or_none(same_file_centered)
    cross_centered = median_or_none(cross_file_centered)

    return {
        "sample_pairs": sample_pairs,
        "centroid_norm": round(vector_norm(centroid), 4),
        "raw_pairwise": describe(raw_values),
        "centered_pairwise": describe(centered_values),
        "same_file_median_raw": same_raw,
        "cross_file_median_raw": cross_raw,
        "same_file_median_centered": same_centered,
        "cross_file_median_centered": cross_centered,
        "same_minus_cross_gap_raw": rounded_difference(same_raw, cross_raw),
        "same_minus_cross_gap_centered": rounded_difference(same_centered, cross_centered),
    }


def benchmark_query(
    label: str,
    query_vec: list[float],
    rows: list[dict[str, Any]],
    centroid: list[float],
) -> dict[str, Any]:
    query_centered = subtract_vector(query_vec, centroid)
    raw_ranked = rank_rows(query_vec, rows)
    centered_ranked = rank_rows(query_centered, rows, centroid=centroid)
    raw_scores = [score for _, score in raw_ranked]
    centered_scores = [score for _, score in centered_ranked]

    return {
        "label": label,
        "candidate_count": len(rows),
        "raw": ranking_metrics(raw_ranked, raw_scores),
        "centered": ranking_metrics(centered_ranked, centered_scores),
        "top10_overlap": overlap(raw_ranked, centered_ranked, 10),
        "top20_overlap": overlap(raw_ranked, centered_ranked, 20),
    }


def rank_rows(
    query_vec: list[float],
    rows: list[dict[str, Any]],
    *,
    centroid: list[float] | None = None,
) -> list[tuple[str, float]]:
    ranked = []
    for row in rows:
        chunk_vec = subtract_vector(row["embedding"], centroid) if centroid else row["embedding"]
        ranked.append((row["chunk_id"], cosine(query_vec, chunk_vec)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def ranking_metrics(ranked: list[tuple[str, float]], scores: list[float]) -> dict[str, Any]:
    return {
        "score_distribution": describe(scores),
        "top1_score": round(ranked[0][1], 4) if ranked else None,
        "top1_top5_gap": top_gap(ranked, 5),
        "top1_top10_gap": top_gap(ranked, 10),
        "top1_top20_gap": top_gap(ranked, 20),
    }


def top_gap(ranked: list[tuple[str, float]], k: int) -> float | None:
    if len(ranked) < k:
        return None
    return round(ranked[0][1] - ranked[k - 1][1], 4)


def overlap(left: list[tuple[str, float]], right: list[tuple[str, float]], k: int) -> float | None:
    if len(left) < k or len(right) < k:
        return None
    left_ids = {chunk_id for chunk_id, _ in left[:k]}
    right_ids = {chunk_id for chunk_id, _ in right[:k]}
    return round(len(left_ids & right_ids) / k, 4)


def describe(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p10": None, "p50": None, "p90": None, "p99": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "p10": round(percentile(values, 10), 4),
        "p50": round(percentile(values, 50), 4),
        "mean": round(statistics.fmean(values), 4),
        "stdev": round(statistics.pstdev(values), 4),
        "p90": round(percentile(values, 90), 4),
        "p99": round(percentile(values, 99), 4),
        "max": round(max(values), 4),
    }


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.median(values), 4)


def rounded_difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_value, right_value in zip(left, right):
        dot += left_value * right_value
        left_norm += left_value * left_value
        right_norm += right_value * right_value
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / ((left_norm ** 0.5) * (right_norm ** 0.5))


def vector_centroid(vectors: list[list[float]]) -> list[float]:
    dim = len(vectors[0])
    centroid = [0.0] * dim
    for vector in vectors:
        for index, value in enumerate(vector):
            centroid[index] += value
    count = float(len(vectors))
    return [value / count for value in centroid]


def subtract_vector(vector: list[float], centroid: list[float] | None) -> list[float]:
    if not centroid:
        return vector
    return [value - centroid[index] for index, value in enumerate(vector)]


def vector_norm(vector: list[float]) -> float:
    return sum(value * value for value in vector) ** 0.5


def render_markdown(report: dict[str, Any]) -> str:
    corpus = report["corpus"]
    raw = corpus["raw_pairwise"]
    centered = corpus["centered_pairwise"]
    lines = [
        "# NOUZ Chunk Scoring Benchmark",
        "",
        "Privacy: this report contains no note paths, titles, headings, or note text.",
        "",
        "## System Intent",
        "",
        "NOUZ is a semantic layer over an Obsidian knowledge base: it keeps a graph of notes, typed hierarchy, reference vectors, and retrieval chunks so AI agents can navigate knowledge by structure rather than by full-text search alone.",
        "",
        "This benchmark checks whether chunk retrieval is dominated by anisotropic embedding background: raw cosine should not make most unrelated chunks look similarly close.",
        "",
        "## Corpus",
        "",
        f"- chunks: {report['db']['chunk_count']}",
        f"- files with chunks: {report['db']['file_count']}",
        f"- embedding dim: {report['db']['embedding_dim']}",
        f"- embedding model: `{report['embedding_model']}`",
        f"- centroid norm: {corpus['centroid_norm']}",
        "",
        "## Pairwise Score Distribution",
        "",
        "| mode | p10 | p50 | mean | p90 | p99 | stdev |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| raw | {raw['p10']} | {raw['p50']} | {raw['mean']} | {raw['p90']} | {raw['p99']} | {raw['stdev']} |",
        f"| centered | {centered['p10']} | {centered['p50']} | {centered['mean']} | {centered['p90']} | {centered['p99']} | {centered['stdev']} |",
        "",
        "## Same-File Signal",
        "",
        f"- raw same-file median minus cross-file median: {corpus['same_minus_cross_gap_raw']}",
        f"- centered same-file median minus cross-file median: {corpus['same_minus_cross_gap_centered']}",
        "",
        "## Query Ranking",
        "",
        "| query label | raw top1-top10 | centered top1-top10 | top10 overlap | raw p50 | centered p50 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for query in report["queries"]:
        raw_query = query["raw"]
        centered_query = query["centered"]
        lines.append(
            "| {label} | {raw_gap} | {centered_gap} | {overlap} | {raw_p50} | {centered_p50} |".format(
                label=query["label"],
                raw_gap=raw_query["top1_top10_gap"],
                centered_gap=centered_query["top1_top10_gap"],
                overlap=query["top10_overlap"],
                raw_p50=raw_query["score_distribution"]["p50"],
                centered_p50=centered_query["score_distribution"]["p50"],
            )
        )

    lines.extend(
        [
            "",
            "## Release Gate",
            "",
            "- Keep `score_mode=raw` for legacy comparison and debugging.",
            "- Use `score_mode=auto` as default for unscoped large candidate sets.",
            "- Treat larger centered score gaps as a healthier ranking surface, not as proof of semantic correctness by itself.",
            "- Use this report as an article-safe numerical slice: it demonstrates the anisotropy problem without exposing private notes.",
            "",
            f"Generated at UTC: {report['generated_at_utc']}",
            f"Elapsed seconds: {report['elapsed_sec']}",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
