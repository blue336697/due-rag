"""BM25 关键词索引 — 中文分词 + 同义词扩展 + 关键词召回 + 磁盘持久化。

按 RAG高级检索能力开发指南 §8.2:
  - BM25 索引落盘: indexes/bm25/<domain>/bm25.pkl + manifest.json
  - cold start 可从磁盘恢复

调用方: retrieval/keyword.py (关键词召回)
"""
from __future__ import annotations

import json
import logging
import pickle
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from service.core.synonyms import expand_query

_logger = logging.getLogger(__name__)

_ISSUE_CODE_RE = re.compile(r"\b[A-Z][A-Z_]{2,}[A-Z]\b")


def extract_cjk_phrases(text: str) -> List[str]:
    """从中文文本中提取有意义的短语用于匹配。"""
    phrases: List[str] = []
    cjk = re.findall(r"[一-鿿]{3,5}", text)
    phrases.extend(cjk)
    segments = re.split(r"[的是了什么呢吗啊呀与和或这那在有不]", text)
    for seg in segments:
        seg = seg.strip()
        seg_len = len(seg)
        if seg_len >= 2:
            phrases.append(seg)
            if seg_len > 4:
                for win in (2, 3, 4):
                    for i in range(seg_len - win + 1):
                        phrases.append(seg[i : i + win])
    return phrases


def extract_issue_codes(text: str) -> List[str]:
    return _ISSUE_CODE_RE.findall(text)


def keyword_score_chunk(
    chunk: Dict[str, Any],
    query_phrases: List[str],
    issue_codes: List[str],
) -> float:
    """基于短语命中的加权打分，含规则 boost。

    - 基础分：长短语命中权重更高
    - 标题 boost：查询短语命中标题 → ×1.5
    - issue code boost：issue code 精确命中 → ×2.0
    """
    heading = chunk.get("heading", "")
    content = chunk.get("content", "")
    text = f"{heading} {content}"

    hits = 0.0
    for phrase in query_phrases:
        if phrase in text:
            hits += len(phrase)

    if hits == 0:
        return 0.0

    raw = hits / max(len(query_phrases), 10)

    if heading and any(phrase in heading for phrase in query_phrases):
        raw *= 1.5

    for code in issue_codes:
        if code in text:
            raw *= 2.0
            break

    return raw


def keyword_recall(
    query: str,
    chunks: List[Dict[str, Any]],
    top_n: int,
) -> List[Dict[str, Any]]:
    """关键词召回：扩展查询 → 提取短语 → 关键词打分 → 排序 → top_n。"""
    expanded = expand_query(query)
    query_phrases = extract_cjk_phrases(expanded)
    issue_codes = extract_issue_codes(query)

    scored: List[Dict[str, Any]] = []
    for chunk in chunks:
        score = keyword_score_chunk(chunk, query_phrases, issue_codes)
        if score > 0:
            scored.append({
                "source": chunk["source"],
                "heading": chunk.get("heading", ""),
                "content": chunk.get("content", ""),
                "keyword_score": round(score, 4),
            })

    scored.sort(key=lambda c: c["keyword_score"], reverse=True)
    return scored[:top_n]


# ═══════════════════════════════════════════════════════════════
# BM25Index — 磁盘持久化 + manifest
# ═══════════════════════════════════════════════════════════════

class BM25Index:
    """BM25 关键词索引 — 封装 chunk 存储、召回和持久化。

    磁盘布局:
      indexes/bm25/<domain>/
        bm25.pkl       — pickled chunk list
        manifest.json  — BM25 子 manifest
    """

    def __init__(self, namespace: str, index_dir: str):
        self._namespace = namespace
        self._index_dir = Path(index_dir)
        self._chunks: List[Dict[str, Any]] = []
        self._manifest: Dict[str, Any] = {}

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def chunks(self) -> List[Dict[str, Any]]:
        return self._chunks

    @property
    def domain_dir(self) -> Path:
        return self._index_dir / "bm25" / self._namespace

    def build(self, chunks: List[Dict[str, Any]], build_hash: str = "") -> None:
        """从 chunk 列表构建内存索引。"""
        self._chunks = chunks
        self._manifest = {
            "namespace": self._namespace,
            "chunk_count": len(chunks),
            "build_hash": build_hash,
            "created_at": datetime.now().isoformat(),
        }
        _logger.info("BM25 index built: ns=%s chunks=%d", self._namespace, len(chunks))

    def save(self) -> None:
        """持久化到磁盘。"""
        save_dir = self.domain_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        # pickle chunks
        with open(save_dir / "bm25.pkl", "wb") as f:
            pickle.dump(self._chunks, f)
        # manifest JSON
        with open(save_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(self._manifest, f, ensure_ascii=False, indent=2)
        _logger.info("BM25 saved: %s (%d chunks)", save_dir, len(self._chunks))

    def load(self) -> bool:
        """从磁盘加载。成功返回 True，不存在或损坏返回 False。"""
        load_dir = self.domain_dir
        pkl_path = load_dir / "bm25.pkl"
        manifest_path = load_dir / "manifest.json"

        if not pkl_path.exists():
            _logger.info("BM25 index not found: %s", pkl_path)
            return False

        try:
            with open(pkl_path, "rb") as f:
                self._chunks = pickle.load(f)
            if manifest_path.exists():
                with open(manifest_path, "r", encoding="utf-8") as f:
                    self._manifest = json.load(f)
            _logger.info("BM25 loaded: %s (%d chunks)", load_dir, len(self._chunks))
            return True
        except Exception:
            _logger.warning("BM25 load failed: %s", load_dir, exc_info=True)
            return False

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """关键词召回。复用 keyword_recall，添加 chunk_id/heading_path 等字段。"""
        results = keyword_recall(query, self._chunks, top_k)
        # 补充 chunk metadata 字段
        for i, r in enumerate(results):
            r.setdefault("chunk_id", "")
            r.setdefault("heading_path", [r.get("heading", "")] if r.get("heading") else [])
            r.setdefault("parent_id", None)
            r.setdefault("content_hash", "")
        return results
