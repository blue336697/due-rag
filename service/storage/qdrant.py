"""向量存储接口 — 抽象层，支持 InMemoryVectorStore (本地) 和 Qdrant (服务器)。

调用方: retrieval/vector.py (向量召回), retrieval/service.py (索引管理)
从 vector_store.py 迁移，移除 LangChain InMemoryVectorStore + HuggingFaceEmbeddings 依赖。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol

import numpy as np

_logger = logging.getLogger(__name__)


class VectorStore(Protocol):
    """向量存储抽象接口。"""

    def search(self, query_vector: List[float], top_k: int, filters: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        """向量相似度搜索，返回 top_k 结果。"""
        ...

    def count(self) -> int:
        """返回已索引的文档数。"""
        ...

    def upsert(self, chunks: List[Dict[str, Any]], vectors: List[List[float]]) -> None:
        """插入或更新向量。"""
        ...


class InMemoryVectorStore:
    """内存向量存储 — 本地开发/测试用，cosine similarity。

    迁移说明: 替代 langchain_core.vectorstores.InMemoryVectorStore。
    服务器部署后用 QdrantVectorStore 替换。
    """

    def __init__(self):
        self._chunks: List[Dict[str, Any]] = []
        self._vectors: List[np.ndarray] = []

    def add(self, chunks: List[Dict[str, Any]], vectors: List[List[float]]) -> None:
        for chunk, vec in zip(chunks, vectors):
            self._chunks.append(chunk)
            self._vectors.append(np.array(vec, dtype=np.float32))

    def search(self, query_vector: List[float], top_k: int, filters: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        if not self._vectors:
            return []
        query = np.array(query_vector, dtype=np.float32)
        query_norm = query / (np.linalg.norm(query) + 1e-8)
        scores = [float(np.dot(query_norm, v / (np.linalg.norm(v) + 1e-8))) for v in self._vectors]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results: List[Dict[str, Any]] = []
        for idx, score in ranked[:top_k]:
            chunk = dict(self._chunks[idx])
            chunk["vector_score"] = round(score, 4)
            results.append(chunk)
        return results

    def count(self) -> int:
        return len(self._chunks)


class QdrantVectorStore:
    """Qdrant 向量存储 — 服务器生产环境。

    按 RAG高级检索能力开发指南 §8.3:
      - Qdrant 只存 child chunk 向量和 child 级定位字段
      - payload 含 chunk_id/parent_id/source/heading_path/content/content_hash 等
      - 必须创建 payload index 用于 filters 和运维排查
    """

    PAYLOAD_INDEX_FIELDS = [
        "source", "heading_path", "category", "knowledge_version",
        "embedding_model", "embedding_revision", "statement_type",
    ]

    def __init__(self, url: str, api_key: str, collection_name: str, timeout: int = 10):
        self._url = url
        self._api_key = api_key
        self._collection_name = collection_name
        self._timeout = timeout
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
                self._client = QdrantClient(url=self._url, api_key=self._api_key, timeout=self._timeout)
            except ImportError:
                raise ImportError("qdrant-client 未安装。")
        return self._client

    def search(self, query_vector: List[float], top_k: int, filters: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        # qdrant-client 1.18+ uses query_points instead of search
        try:
            from qdrant_client.models import QueryRequest
            results = self.client.query_points(
                collection_name=self._collection_name,
                query=query_vector,
                limit=top_k,
                query_filter=filters,
            )
            return [{"chunk_id": r.id, "score": round(r.score, 4), **r.payload} for r in results.points]
        except AttributeError:
            # Fallback for older versions
            results = self.client.search(
                collection_name=self._collection_name,
                query_vector=query_vector,
                limit=top_k,
                query_filter=filters,
            )
            return [{"chunk_id": r.id, "score": round(r.score, 4), **r.payload} for r in results]

    def count(self) -> int:
        try:
            info = self.client.get_collection(self._collection_name)
            return info.points_count
        except Exception:
            return 0

    def upsert(self, chunks: List[Dict[str, Any]], vectors: List[List[float]]) -> None:
        try:
            from qdrant_client.models import PointStruct
            import uuid
            points = []
            for i, (c, vec) in enumerate(zip(chunks, vectors)):
                # Qdrant requires integer or UUID format for point ID
                # Use deterministic UUID based on chunk_id hash
                chunk_id = c.get("chunk_id", f"chunk_{i}")
                try:
                    # Generate deterministic UUID from chunk_id
                    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
                except Exception:
                    point_id = str(uuid.uuid4())

                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vec,
                        payload=self._build_payload(c),
                    )
                )
            self.client.upsert(collection_name=self._collection_name, points=points)
            _logger.info("Qdrant upsert: %d points → %s", len(points), self._collection_name)
        except Exception:
            _logger.warning("Qdrant upsert failed", exc_info=True)
            raise

    def create_payload_indexes(self) -> None:
        """为 filters 必用字段创建 Qdrant payload index。"""
        try:
            from qdrant_client.models import PayloadSchemaType
            for field in self.PAYLOAD_INDEX_FIELDS:
                try:
                    self.client.create_payload_index(
                        collection_name=self._collection_name,
                        field_name=field,
                        field_schema=PayloadSchemaType.KEYWORD,
                        wait=False,
                    )
                except Exception:
                    pass
            _logger.info("Qdrant payload indexes ensured: %s", self._collection_name)
        except Exception:
            _logger.warning("Qdrant payload index creation failed", exc_info=True)

    @staticmethod
    def _build_payload(chunk: Dict[str, Any]) -> Dict[str, Any]:
        """构建 Qdrant point payload（§8.3 完整字段清单）。"""
        meta = chunk.get("metadata", {}) if isinstance(chunk.get("metadata"), dict) else {}
        return {
            "chunk_id": chunk.get("chunk_id", ""),
            "parent_id": chunk.get("parent_id"),
            "source": chunk.get("source", ""),
            "heading": chunk.get("title") or (chunk.get("heading_path", [])[-1] if chunk.get("heading_path") else ""),
            "heading_path": chunk.get("heading_path", []),
            "content": chunk.get("content", ""),
            "content_hash": chunk.get("content_hash", ""),
            "knowledge_version": meta.get("knowledge_version", ""),
            "chunk_version": meta.get("chunk_version", ""),
            "embedding_model": meta.get("embedding_model_id", ""),
            "embedding_revision": meta.get("embedding_revision", ""),
            "prev_chunk_id": chunk.get("prev_chunk_id"),
            "next_chunk_id": chunk.get("next_chunk_id"),
            "chunk_type": chunk.get("chunk_type", "child"),
            "ordinal": chunk.get("ordinal", 0),
            "category": meta.get("category"),
            "statement_type": meta.get("statement_type"),
            "entities": meta.get("entities", []),
        }
