"""Embedding 模型加载 — SentenceTransformer 封装，替代 langchain_huggingface.HuggingFaceEmbeddings。

调用方: retrieval/vector.py (向量召回), storage/qdrant.py (索引构建)
"""
from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np

_logger = logging.getLogger(__name__)


class EmbeddingModel:
    """SentenceTransformer 封装，提供 encode 接口。

    迁移说明: 直接使用 sentence-transformers SentenceTransformer，移除 LangChain 依赖。
    """

    def __init__(self, model_name: str, device: str = "cpu", normalize: bool = True):
        self._model_name = model_name
        self._device = device
        self._normalize = normalize
        self._model: Optional[object] = None

    @property
    def model(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name, device=self._device)
                _logger.info("Embedding model loaded: %s (device=%s, dim=%d)",
                            self._model_name, self._device, self.dim)
            except ImportError:
                raise ImportError("sentence-transformers 未安装。运行: pip install sentence-transformers")
        return self._model

    @property
    def dim(self) -> int:
        return self.model.get_sentence_embedding_dimension()

    def encode(self, texts: List[str]) -> List[List[float]]:
        """编码文本为向量列表。"""
        if not texts:
            return []
        embeddings = self.model.encode(
            texts,
            normalize_embeddings=self._normalize,
            show_progress_bar=False,
        )
        if isinstance(embeddings, np.ndarray):
            return embeddings.tolist()
        return [e.tolist() if hasattr(e, "tolist") else list(e) for e in embeddings]

    def encode_query(self, query: str) -> List[float]:
        """编码单条查询文本。"""
        result = self.encode([query])
        return result[0] if result else []
