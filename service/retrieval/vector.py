"""向量召回模块。

按 RAG高级检索能力开发指南 §3: 任何失败都要显式错误化。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

_logger = logging.getLogger(__name__)


class VectorRetrievalError(Exception):
    """向量检索失败 — embedding 模型不可用或向量存储异常。"""
    pass


def retrieve_vector(
    query: str,
    vector_store: Any,
    embedding_model: Any,
    top_k: int,
) -> List[Dict[str, Any]]:
    """向量召回入口。

    Raises:
        VectorRetrievalError: vector_store/embedding_model 不可用或检索失败
    """
    if vector_store is None:
        raise VectorRetrievalError("vector_store 未初始化")
    if embedding_model is None:
        raise VectorRetrievalError("embedding_model 未初始化")

    try:
        query_vec = embedding_model.encode_query(query)
        if not query_vec:
            raise VectorRetrievalError("embedding 编码返回空向量")
    except VectorRetrievalError:
        raise
    except Exception as e:
        raise VectorRetrievalError(f"embedding 编码失败: {e}") from e

    try:
        results = vector_store.search(query_vec, top_k)
    except Exception as e:
        raise VectorRetrievalError(f"向量搜索失败: {e}") from e

    for r in results:
        r.setdefault("vector_score", r.get("score", 0.0))
    return results
