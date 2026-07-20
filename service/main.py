"""RAG Service — FastAPI 独立服务入口。

启动: uvicorn service.main:app --host 0.0.0.0 --port 8020
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    """Startup: 初始化 retrieval service。Shutdown: 清理资源。"""
    _logger.info("RAG Service starting...")
    try:
        _init_retrieval_service(_app)
        _logger.info("RAG Service started")
    except Exception:
        _logger.warning("RAG Service startup incomplete", exc_info=True)
    yield
    _logger.info("RAG Service shutting down...")


def _init_retrieval_service(app: FastAPI) -> None:
    """初始化 RetrievalService 并挂载到 app.state。"""
    from service.core.config import get_config, get_knowledge_dir, get_managed_knowledge_dir, get_collection_name
    from service.storage.manifest_store import load_manifest
    from service.retrieval.service import RetrievalService

    cfg = get_config()

    # 构建 domain -> knowledge_dir 映射
    knowledge_dirs: dict[str, str] = {}
    managed_knowledge_dirs: dict[str, str] = {}
    for domain, domain_cfg in cfg["knowledge"]["domains"].items():
        if not domain_cfg.get("enabled", True):
            continue
        knowledge_dirs[domain] = get_knowledge_dir(domain)
        managed_knowledge_dirs[domain] = get_managed_knowledge_dir(domain)

    # 初始化 embedding model
    embedding_model = None
    try:
        from service.models.embedding import EmbeddingModel
        emb_cfg = cfg["embedding"]
        embedding_model = EmbeddingModel(
            model_name=emb_cfg["model"],
            device=emb_cfg["device"],
            normalize=emb_cfg["normalize"],
        )
        _logger.info("Embedding model loaded: %s", emb_cfg["model"])
    except Exception as e:
        _logger.warning("Embedding model not available: %s", e)

    # 初始化 vector store
    vector_store = None
    persisted_manifest = None
    if embedding_model is not None:
        # 优先使用 Qdrant（生产环境），否则回退到内存存储
        qdrant_url = cfg["qdrant"]["url"]
        if qdrant_url:
            try:
                from service.storage.qdrant import QdrantVectorStore
                domain = next(iter(knowledge_dirs), None)
                if domain:
                    persisted_manifest = load_manifest(cfg["paths"]["index_dir"], domain)
                collection = (
                    persisted_manifest.collection
                    if persisted_manifest and persisted_manifest.collection
                    else get_collection_name(domain) if domain else "rag_knowledge"
                )
                vector_store = QdrantVectorStore(
                    url=qdrant_url,
                    api_key=os.getenv(cfg["qdrant"]["api_key_env"], ""),
                    collection_name=collection,
                    timeout=cfg["qdrant"]["timeout"],
                )
                # 确保collection存在
                try:
                    from qdrant_client.models import VectorParams, Distance
                    vector_store.client.create_collection(
                        collection_name=collection,
                        vectors_config=VectorParams(
                            size=cfg["embedding"]["dim"],
                            distance=Distance.COSINE,
                        ),
                    )
                    _logger.info("Qdrant collection created/verified: %s", collection)
                except Exception as e:
                    # collection可能已存在
                    _logger.debug("Qdrant collection check: %s", e)
                vector_store.create_payload_indexes()
                _logger.info("Using Qdrant vector store: %s (collection=%s)", qdrant_url, collection)
            except Exception as e:
                # 显式配置了 Qdrant 时不能静默写入易失性内存索引，否则 job 会伪成功。
                _logger.error("Configured Qdrant is unavailable: %s", e)
                vector_store = None
        else:
            from service.storage.qdrant import InMemoryVectorStore
            vector_store = InMemoryVectorStore()
            _logger.info("Using in-memory vector store (no Qdrant URL configured)")

        # 已有持久化 generation 时直接加载；首次启动才走兼容性初始化。
        persisted_vector_ready = bool(
            persisted_manifest and vector_store is not None and vector_store.count() > 0
        )
        for domain, knowledge_dir in knowledge_dirs.items():
            if persisted_vector_ready:
                _logger.info("Using persisted vector generation: domain=%s collection=%s", domain, persisted_manifest.collection)
                continue
            try:
                from service.knowledge.loader import load_knowledge_chunks
                from service.core.synonyms import build_synonym_tags
                chunks = load_knowledge_chunks(knowledge_dir)
                managed_dir = managed_knowledge_dirs.get(domain, "")
                if managed_dir and Path(managed_dir).exists():
                    chunks.extend(load_knowledge_chunks(managed_dir, source_prefix="_managed"))
                # 同义词增强 — 写入 embedding_text，禁止污染 content
                for chunk in chunks:
                    original = str(chunk["content"])
                    tags = build_synonym_tags(original)
                    chunk["embedding_text"] = f"{original}\n{tags}" if tags else original

                if chunks:
                    texts = [str(c["content"]) for c in chunks]
                    vectors = embedding_model.encode(texts)
                    if hasattr(vector_store, 'upsert'):
                        # Qdrant
                        vector_store.upsert(chunks, vectors)
                    else:
                        # InMemory
                        vector_store.add(chunks, vectors)
                    _logger.info("Vector index built for domain=%s: %d chunks", domain, len(chunks))
            except Exception as e:
                _logger.warning("Failed to build index for domain=%s: %s", domain, e)

    # 初始化 reranker
    reranker_model = None
    try:
        from service.models.reranker import RerankerModel
        rerank_cfg = cfg["reranker"]
        reranker_model = RerankerModel(
            model_name=rerank_cfg["model"],
            device=rerank_cfg["device"],
            max_length=rerank_cfg["max_length"],
        )
        _logger.info("Reranker model loaded: %s", rerank_cfg["model"])
    except Exception as e:
        _logger.warning("Reranker model not available: %s", e)

    # 构建 LLM 配置
    llm_config = {
        "base_url": cfg["llm"]["base_url"],
        "api_key": os.getenv(cfg["llm"]["api_key_env"], ""),
        "model": cfg["llm"]["model"],
        "timeout_seconds": cfg["llm"]["timeout_seconds"],
    }

    # 创建 RetrievalService
    retrieval_service = RetrievalService(
        knowledge_dirs=knowledge_dirs,
        domain_configs=cfg["knowledge"]["domains"],
        vector_store=vector_store,
        embedding_model=embedding_model,
        reranker_model=reranker_model,
        retrieval_config=cfg["retrieval"],
        llm_config=llm_config,
        managed_knowledge_dirs=managed_knowledge_dirs,
    )
    retrieval_service.load_persisted_indexes()
    app.state.retrieval_service = retrieval_service
    # 初始化持久化 ingestion 队列，并恢复上次重启前未完成的任务。
    from service.ingestion.jobs import get_ingestion_job_manager
    get_ingestion_job_manager()


# ═══════════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="Payment Agent AI — RAG Service",
    description="独立 RAG 服务：知识库加载、检索、重排、问答",
    version="0.1.0",
    lifespan=lifespan,
)

# 鉴权配置在本地可为 none，生产可切换为 header。
from service.core.config import get_config
from service.core.security import AuthMiddleware

_security_cfg = get_config()["service"]
app.add_middleware(
    AuthMiddleware,
    auth_mode=_security_cfg.get("auth_mode", "none"),
    api_key_env=_security_cfg.get("api_key_env", "RAG_SERVICE_API_KEY"),
)

# 路由注册
from service.api.health import router as health_router
from service.api.rag import router as rag_router
from service.api.admin import router as admin_router
from service.api.documents import router as documents_router

app.include_router(health_router)
app.include_router(rag_router)
app.include_router(admin_router)
app.include_router(documents_router)
