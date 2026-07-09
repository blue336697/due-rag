"""CrossEncoder Reranker — 直接封装 sentence-transformers CrossEncoder。

调用方: retrieval/rerank.py
从 cross_encoder_compressor.py 迁移，移除 LangChain BaseDocumentCompressor 依赖。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence

_logger = logging.getLogger(__name__)


class RerankerModel:
    """Cross-Encoder 重排序模型，直接封装 sentence-transformers CrossEncoder。

    迁移说明: 移除 LangChain BaseDocumentCompressor + Document 类型依赖。
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-base", device: str = "cpu", max_length: int = 512):
        self._model_name = model_name
        self._device = device
        self._max_length = max_length
        self._model: Optional[object] = None

    @property
    def model(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(
                    self._model_name,
                    device=self._device,
                    max_length=self._max_length,
                )
                _logger.info("Reranker model loaded: %s (device=%s)", self._model_name, self._device)
            except ImportError:
                raise ImportError("sentence-transformers 未安装。运行: pip install sentence-transformers")
        return self._model

    def rerank(self, query: str, texts: List[str], candidates: List[dict], top_k: int) -> List[dict]:
        """对候选列表按 (query, enriched_text) 相关性重排序。

        Args:
            query: 查询文本
            texts: 与 candidates 一一对应的 enriched chunk text
            candidates: 原始候选列表
            top_k: 保留结果数

        Returns:
            重排序后的 candidates，附带 rerank_score

        Raises:
            RuntimeError: CrossEncoder 推理失败
        """
        if not candidates:
            return []

        pairs = [(query, texts[i] if i < len(texts) else candidates[i].get("content", "")) for i in range(len(candidates))]
        scores = self.model.predict(pairs)

        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: float(x[1]), reverse=True)

        result: List[dict] = []
        for candidate, score in scored[:top_k]:
            candidate["rerank_score"] = round(float(score), 4)
            candidate["score"] = candidate["rerank_score"]
            result.append(candidate)
        return result
        return result
