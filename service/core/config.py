"""RAG Service 配置加载 — 读取 rag_service.yaml + 环境变量覆盖。"""
from __future__ import annotations
import os, re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict
import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_CONFIG_PATH = _CONFIG_DIR / "rag_service.yaml"
_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")

def _resolve_env(value: str) -> str:
    def _replace(m: re.Match) -> str:
        return os.getenv(m.group(1), m.group(2) or "")
    return _VAR_PATTERN.sub(_replace, value)

def _resolve_value(value: Any) -> Any:
    if isinstance(value, str):
        return _resolve_env(value)
    if isinstance(value, dict):
        return {key: _resolve_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item) for item in value]
    return value

def _load_raw() -> Dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

@lru_cache(maxsize=1)
def get_config() -> Dict[str, Any]:
    raw = _load_raw()
    server = raw.get("server", raw.get("service", {}))
    paths = raw.get("paths", {})
    domains = raw.get("domains", raw.get("knowledge", {}).get("domains", {}))
    knowledge = raw.get("knowledge", {})
    models = raw.get("models", {})
    embedding = models.get("embedding", raw.get("embedding", {}))
    reranker = models.get("reranker", raw.get("reranker", {}))
    llm = raw.get("llm", {})
    retrieval = raw.get("retrieval", {})
    qdrant = raw.get("qdrant", {})
    redis_cfg = raw.get("redis", {})
    bm25 = raw.get("bm25", {})
    chunking = raw.get("chunking", {})
    small_to_big = raw.get("small_to_big", {})
    context_enrich = raw.get("context_enrich", {})
    reindex = raw.get("reindex", {})
    runtime = raw.get("runtime", {})
    eval_cfg = raw.get("eval", {})
    tokenizers = raw.get("tokenizers", {})
    ingestion = raw.get("ingestion", {})
    knowledge_root = _resolve_value(paths.get("knowledge_root", knowledge.get("base_dir", "backend/knowledge_base")))
    managed_knowledge_root = _resolve_value(paths.get("managed_knowledge_root", ".local/managed_knowledge"))
    top_k_keyword = int(retrieval.get("top_k_keyword", retrieval.get("keyword_recall_top", 20)))
    top_k_vector = int(retrieval.get("top_k_vector", retrieval.get("vector_recall_top", 40)))
    top_k_rerank = int(retrieval.get("top_k_rerank", retrieval.get("rerank_top_k", 5)))
    return {
        "service": {
            "host": _resolve_value(server.get("host", "0.0.0.0")),
            "port": int(server.get("port", 8020)),
            "api_key_env": server.get("api_key_env", "RAG_SERVICE_API_KEY"),
            "auth_mode": _resolve_value(server.get("auth_mode", "none")),
        },
        "knowledge": {
            "base_dir": knowledge_root,
            "domains": domains,
        },
        "paths": {
            "knowledge_root": knowledge_root,
            "managed_knowledge_root": managed_knowledge_root,
            "index_dir": _resolve_value(paths.get("index_dir", "backend/rag-service/.local/indexes")),
            "bm25_index_dir": _resolve_value(paths.get("bm25_index_dir", "backend/rag-service/.local/indexes/bm25")),
            "model_lock_file": _resolve_value(paths.get("model_lock_file", "backend/rag-service/.local/indexes/manifests/models.lock.json")),
            "ingestion_dir": _resolve_value(paths.get("ingestion_dir", ".local/ingestion")),
        },
        "embedding": {
            "model": os.getenv("RAG_EMBEDDING_MODEL", _resolve_value(embedding.get("path", embedding.get("model", "BAAI/bge-small-zh-v1.5")))),
            "model_id": embedding.get("model_id", "BAAI/bge-small-zh-v1.5"),
            "revision": _resolve_value(embedding.get("revision", "")),
            "dim": int(embedding.get("dim", 512)),
            "device": _resolve_value(embedding.get("device", "cpu")),
            "normalize": embedding.get("normalize", True),
            "semantic_similarity_threshold": float(embedding.get("semantic_similarity_threshold", 0.62)),
        },
        "reranker": {
            "model": os.getenv("RAG_RERANKER_MODEL", _resolve_value(reranker.get("path", reranker.get("model", "BAAI/bge-reranker-base")))),
            "model_id": reranker.get("model_id", "BAAI/bge-reranker-base"),
            "revision": _resolve_value(reranker.get("revision", "")),
            "device": _resolve_value(reranker.get("device", "cpu")),
            "max_length": int(reranker.get("max_length", 512)),
        },
        "llm": {
            "base_url": _resolve_env(llm.get("base_url", "")),
            "api_key_env": llm.get("api_key_env", "LLM_API_KEY"),
            "model": _resolve_env(llm.get("model", "")),
            "timeout_seconds": int(llm.get("timeout_seconds", 60)),
        },
        "retrieval": {
            "top_k_keyword": top_k_keyword,
            "top_k_vector": top_k_vector,
            "top_k_rerank": top_k_rerank,
            # 过渡兼容旧实现字段；新代码应使用 top_k_*。
            "keyword_recall_top": top_k_keyword,
            "vector_recall_top": top_k_vector,
            "rrf_k": int(retrieval.get("rrf_k", 60)),
            "rrf_top_n": int(retrieval.get("rrf_top_n", 10)),
            "rerank_top_k": top_k_rerank,
            "dedup_jaccard_threshold": float(retrieval.get("dedup_jaccard_threshold", 0.8)),
            "default_top_k": int(retrieval.get("default_top_k", 5)),
            "allow_rerank_fallback": bool(retrieval.get("allow_rerank_fallback", False)),
            "max_quote_chars": int(retrieval.get("max_quote_chars", 160)),
        },
        "chunking": chunking,
        "small_to_big": small_to_big,
        "context_enrich": context_enrich,
        "reindex": reindex,
        "runtime": {
            "search_snapshot_mode": runtime.get("search_snapshot_mode", "rcu"),
            "model_executor": runtime.get("model_executor", "threadpool"),
            "embedding_max_concurrency": int(runtime.get("embedding_max_concurrency", 1)),
            "reranker_max_concurrency": int(runtime.get("reranker_max_concurrency", 1)),
            "manifest_reload": runtime.get("manifest_reload", "redis_pubsub_with_polling_fallback"),
            "manifest_poll_interval_seconds": float(runtime.get("manifest_poll_interval_seconds", 2)),
        },
        "eval": eval_cfg,
        "tokenizers": tokenizers,
        "domains": domains,
        "models": models,
        "server": server,
        "qdrant": {
            "url": _resolve_env(qdrant.get("url", "")),
            "api_key_env": qdrant.get("api_key_env", "QDRANT_API_KEY"),
            "prefer_grpc": qdrant.get("prefer_grpc", False),
            "timeout": int(qdrant.get("timeout", 10)),
            "vector_size": int(qdrant.get("vector_size", 512)),
            "distance": qdrant.get("distance", "Cosine"),
            "keep_versions": int(qdrant.get("keep_versions", 2)),
        },
        "redis": {
            "url": _resolve_env(redis_cfg.get("url", "")),
            "host": _resolve_env(redis_cfg.get("host", "localhost")),
            "port": int(redis_cfg.get("port", 6379)),
            "db": int(redis_cfg.get("db", 0)),
            "password_env": redis_cfg.get("password_env", "REDIS_PASSWORD"),
        },
        "bm25": {
            "jieba_dict": bm25.get("jieba_dict", ""),
            "stop_words": bm25.get("stop_words", []),
        },
        "ingestion": {
            "max_upload_bytes": int(ingestion.get("max_upload_bytes", 52428800)),
            "allowed_extensions": list(ingestion.get("allowed_extensions", [".md", ".txt", ".html", ".htm", ".docx", ".pdf", ".png", ".jpg", ".jpeg"])),
            "min_text_chars": int(ingestion.get("min_text_chars", 50)),
            "auto_publish": bool(ingestion.get("auto_publish", False)),
            "ocr": dict(_resolve_value(ingestion.get("ocr", {}))),
        },
    }

def get_knowledge_dir(domain: str) -> str:
    cfg = get_config()
    base = Path(cfg["knowledge"]["base_dir"])
    if not base.is_absolute():
        base = Path(__file__).resolve().parents[2] / base
    domain_cfg = cfg["knowledge"]["domains"].get(domain, {})
    explicit_dir = _resolve_value(domain_cfg.get("knowledge_dir", ""))
    if explicit_dir:
        explicit_path = Path(explicit_dir)
        if explicit_path.is_absolute():
            return str(explicit_path)
    domain_dir = domain_cfg.get("dir", "")
    return str(base / domain_dir) if domain_dir else str(base)


def get_managed_knowledge_dir(domain: str) -> str:
    """返回系统发布文档的独立可写目录，并阻止 domain 逃逸 managed 根目录。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", domain):
        raise ValueError(f"invalid managed knowledge domain: {domain}")
    cfg = get_config()
    if domain not in cfg["knowledge"]["domains"]:
        raise ValueError(f"unknown domain: {domain}")
    base = Path(cfg["paths"]["managed_knowledge_root"])
    if not base.is_absolute():
        base = Path(__file__).resolve().parents[2] / base
    base = base.resolve()
    target = (base / domain).resolve()
    if target.parent != base:
        raise ValueError(f"managed knowledge path escapes root: {domain}")
    source = Path(get_knowledge_dir(domain)).resolve()
    if target == source or source in target.parents or target in source.parents:
        raise ValueError("managed knowledge directory must be outside the read-only source knowledge directory")
    return str(target)

def get_collection_name(domain: str) -> str:
    cfg = get_config()
    domain_cfg = cfg["knowledge"]["domains"].get(domain, {})
    return domain_cfg.get("collection") or domain_cfg.get("bm25_namespace", f"{domain}_knowledge")
