"""RAG Service — FastAPI 独立服务入口。

启动: uvicorn service.main:app --host 0.0.0.0 --port 8020
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
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
    from service.core.config import get_config, get_knowledge_dir
    from service.retrieval.service import RetrievalService

    cfg = get_config()

    # 构建 domain -> knowledge_dir 映射
    knowledge_dirs: dict[str, str] = {}
    for domain, domain_cfg in cfg["knowledge"]["domains"].items():
        if not domain_cfg.get("enabled", True):
            continue
        knowledge_dirs[domain] = get_knowledge_dir(domain)

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
    if embedding_model is not None:
        from service.storage.qdrant import InMemoryVectorStore
        vector_store = InMemoryVectorStore()
        # 为每个 domain 构建向量索引
        for domain, knowledge_dir in knowledge_dirs.items():
            try:
                from service.knowledge.loader import load_knowledge_chunks
                from service.core.synonyms import build_synonym_tags
                chunks = load_knowledge_chunks(knowledge_dir)
                # 同义词增强 — 写入 embedding_text，禁止污染 content
                for chunk in chunks:
                    original = str(chunk["content"])
                    tags = build_synonym_tags(original)
                    chunk["embedding_text"] = f"{original}\n{tags}" if tags else original

                if chunks:
                    texts = [str(c["content"]) for c in chunks]
                    vectors = embedding_model.encode(texts)
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
    )
    app.state.retrieval_service = retrieval_service


# ═══════════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="Payment Agent AI — RAG Service",
    description="独立 RAG 服务：知识库加载、检索、重排、问答",
    version="0.1.0",
    lifespan=lifespan,
)

# 路由注册
from service.api.health import router as health_router
from service.api.rag import router as rag_router
from service.api.admin import router as admin_router

app.include_router(health_router)
app.include_router(rag_router)
app.include_router(admin_router)
