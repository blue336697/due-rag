"""RRF 融合 + Jaccard 去重 — 从 hybrid_retriever.py 拆分。

调用方: retrieval/service.py (hybrid 检索流程)
"""
from __future__ import annotations

from typing import Any, Dict, List, Set


def jaccard_similarity(text_a: str, text_b: str) -> float:
    """计算两段文本的 2-gram Jaccard 相似度。"""
    def bigrams(s: str) -> Set[str]:
        return {s[i : i + 2] for i in range(len(s) - 1)}
    set_a = bigrams(text_a)
    set_b = bigrams(text_b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def merge_dedup(
    kw_results: List[Dict[str, Any]],
    vec_results: List[Dict[str, Any]],
    threshold: float = 0.8,
) -> List[Dict[str, Any]]:
    """合并关键词和向量结果，重复项合并字段和两路召回分数。"""
    merged: List[Dict[str, Any]] = []
    seen_contents: List[str] = []

    for r in kw_results + vec_results:
        content = r.get("content", "")
        duplicate_index = next(
            (
                index
                for index, seen_content in enumerate(seen_contents)
                if jaccard_similarity(content, seen_content) > threshold
            ),
            None,
        )
        if duplicate_index is None:
            seen_contents.append(content)
            merged.append(dict(r))
            continue

        existing = merged[duplicate_index]
        for key, value in r.items():
            if key in {"keyword_score", "vector_score"}:
                existing[key] = value
            elif key not in existing or existing[key] in (None, "", [], {}):
                existing[key] = value

    return merged


def rrf_fusion(
    merged: List[Dict[str, Any]],
    k: int = 60,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """倒数排名融合：rrf_score = 1/(k+keyword_rank) + 1/(k+vector_rank)。

    只在单个来源中出现的文档，另一来源贡献 0。
    """
    # keyword 排名
    kw_candidates = sorted(
        [r for r in merged if "keyword_score" in r],
        key=lambda r: r.get("keyword_score", 0),
        reverse=True,
    )
    kw_rank: Dict[int, int] = {}
    for rank, r in enumerate(kw_candidates, start=1):
        kw_rank[id(r)] = rank

    # vector 排名
    vec_candidates = sorted(
        [r for r in merged if "vector_score" in r],
        key=lambda r: r.get("vector_score", 0),
        reverse=True,
    )
    vec_rank: Dict[int, int] = {}
    for rank, r in enumerate(vec_candidates, start=1):
        vec_rank[id(r)] = rank

    for r in merged:
        kw_r = kw_rank.get(id(r))
        vec_r = vec_rank.get(id(r))

        rrf = 0.0
        if kw_r is not None:
            rrf += 1.0 / (k + kw_r)
            r["keyword_rank"] = kw_r
        if vec_r is not None:
            rrf += 1.0 / (k + vec_r)
            r["vector_rank"] = vec_r
        r["rrf_score"] = round(rrf, 6)

    merged.sort(key=lambda r: r.get("rrf_score", 0), reverse=True)
    return merged[:top_n]
