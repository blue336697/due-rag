"""离线索引构建入口 — 与 /admin/reindex 共用同一套 pipeline。

用法: python -m service.scripts.build_index --domain bank_stmt

按 RAG高级检索能力开发指南 §7: 该脚本是离线索引入口，必须实现。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_logger = logging.getLogger("build_index")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG Service 离线索引构建")
    parser.add_argument("--domain", default="bank_stmt", help="业务域")
    args = parser.parse_args()

    from service.core.config import get_config, get_knowledge_dir
    from service.models.embedding import EmbeddingModel
    from service.storage.qdrant import InMemoryVectorStore
    from service.retrieval.service import RetrievalService

    cfg = get_config()
    domain = args.domain
    domain_cfg = cfg["knowledge"]["domains"].get(domain, {})
    if not domain_cfg.get("enabled", True):
        _logger.error("Domain disabled: %s", domain)
        sys.exit(1)

    knowledge_dir = get_knowledge_dir(domain)
    emb_cfg = cfg["embedding"]
    embedding_model = EmbeddingModel(model_name=emb_cfg["model"], device=emb_cfg["device"], normalize=emb_cfg["normalize"])

    retrieval_service = RetrievalService(
        knowledge_dirs={domain: knowledge_dir},
        domain_configs=cfg["knowledge"]["domains"],
        vector_store=InMemoryVectorStore(),
        embedding_model=embedding_model,
        reranker_model=None,
        retrieval_config=cfg["retrieval"],
        llm_config=cfg.get("llm", {}),
    )

    result = retrieval_service.reindex_domain(domain)
    _logger.info("Build complete: %s", result)


if __name__ == "__main__":
    main()
