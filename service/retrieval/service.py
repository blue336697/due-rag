"""检索服务 — RAG Service 内部统一检索入口 + 问答。

按 RAG高级检索能力开发指南 §8:
  - Domain pipeline profile 分派: hybrid_advanced / keyword_simple
  - score_breakdown + citation builder 集成
  - context_enrich + rerank 按 domain 配置执行

调用方: api/rag.py, api/admin.py, api/health.py, service/main.py
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from service.knowledge.loader import load_knowledge_chunks
from service.retrieval.keyword import retrieve_keyword
from service.retrieval.vector import VectorRetrievalError, retrieve_vector
from service.retrieval.fusion import merge_dedup, rrf_fusion
from service.retrieval.rerank import RerankerError, rerank_candidates
from service.retrieval.context_enrich import enrich_context
from service.retrieval.citation import build_citations
from service.schemas.rag import Chunk

_logger = logging.getLogger(__name__)

_PROFILE_MAP: Dict[str, str] = {
    "bank_stmt": "hybrid_advanced",
    "config_gen": "keyword_simple",
}

_PROFILE_MODES: Dict[str, List[str]] = {
    "hybrid_advanced": ["hybrid", "keyword", "vector"],
    "keyword_simple": ["keyword"],
}


class RetrievalService:
    """RAG 检索服务 — 多 domain 知识库管理，pipeline profile 分派。"""

    def __init__(
        self,
        knowledge_dirs: Dict[str, str],
        domain_configs: Dict[str, Dict[str, Any]],
        vector_store: Any,
        embedding_model: Any,
        reranker_model: Any,
        retrieval_config: Dict[str, Any],
        llm_config: Dict[str, Any],
    ):
        self._knowledge_dirs = knowledge_dirs
        self._domain_configs = domain_configs
        self._vector_store = vector_store
        self._embedding_model = embedding_model
        self._reranker_model = reranker_model
        self._retrieval_config = retrieval_config
        self._llm_config = llm_config
        self._chunk_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._chunks_by_id: Dict[str, Dict[str, Chunk]] = {}
        self._domain_snapshots: Dict[str, Dict[str, Any]] = {}

    @property
    def keyword_available(self) -> bool: return True

    @property
    def vector_available(self) -> bool:
        return self._vector_store is not None and self._embedding_model is not None

    @property
    def rerank_available(self) -> bool:
        return self._reranker_model is not None

    @property
    def mode(self) -> str:
        return "hybrid" if self.vector_available else "keyword"

    def _get_chunks(self, domain: str) -> List[Dict[str, Any]]:
        if domain not in self._chunk_cache:
            knowledge_dir = self._knowledge_dirs.get(domain, "")
            self._chunk_cache[domain] = load_knowledge_chunks(knowledge_dir) if knowledge_dir else []
        return self._chunk_cache[domain]

    def _get_profile(self, domain: str) -> str:
        return self._domain_configs.get(domain, {}).get(
            "pipeline_profile", _PROFILE_MAP.get(domain, "keyword_simple")
        )

    def _get_domain_cfg(self, domain: str) -> Dict[str, Any]:
        return self._domain_configs.get(domain, {})

    # ── 检索入口 ──

    def search(
        self,
        query: str,
        domain: str = "bank_stmt",
        top_k: int = 5,
        mode: str = "hybrid",
        filters: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        profile = self._get_profile(domain)

        allowed_modes = _PROFILE_MODES.get(profile, ["keyword"])
        if mode not in allowed_modes:
            raise ValueError(f"mode_not_supported_for_profile: domain={domain} profile={profile} mode={mode}")

        if profile == "keyword_simple":
            results, _meta = self._search_keyword_simple(query, domain, top_k)
        else:
            results, _meta = self._search_hybrid_advanced(query, domain, top_k, mode)

        # citation builder — write back to each result dict
        citations = build_citations(results)
        for i, r in enumerate(results):
            if i < len(citations):
                r["citations"] = [citations[i].model_dump() if hasattr(citations[i], "model_dump") else citations[i]]

        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "results": results,
            "retriever": {
                "mode": mode,
                "keyword_available": self.keyword_available,
                "vector_available": self.vector_available,
                "rerank_available": self.rerank_available,
            },
            "latency_ms": round(elapsed, 1),
        }

    # ── keyword_simple ──

    def _search_keyword_simple(self, query: str, domain: str, top_k: int):
        chunks = self._get_chunks(domain)
        results = retrieve_keyword(query, chunks, top_k)
        for r in results:
            ks = r.get("keyword_score")
            r.setdefault("score", ks if ks is not None else 0.0)
            r.setdefault("score_breakdown", {
                "keyword_score": ks,
                "vector_score": None,
                "rrf_score": None,
                "rerank_score": None,
                "final_score": ks,
            })
        return results, {"profile": "keyword_simple"}

    # ── hybrid_advanced ──

    def _search_hybrid_advanced(self, query: str, domain: str, top_k: int, mode: str):
        cfg = self._retrieval_config
        domain_cfg = self._get_domain_cfg(domain)
        chunks = self._get_chunks(domain)

        kw_top = cfg.get("top_k_keyword", cfg.get("keyword_recall_top", 20))
        vec_top = cfg.get("top_k_vector", cfg.get("vector_recall_top", 40))
        rrf_k = cfg.get("rrf_k", 60)
        rrf_n = cfg.get("rrf_top_n", 10)
        top_k_rerank = cfg.get("top_k_rerank", 5)
        dedup_threshold = cfg.get("dedup_jaccard_threshold", 0.8)
        allow_fallback = cfg.get("allow_rerank_fallback", False)
        enable_ce = domain_cfg.get("enable_context_enrich", True)
        small_to_big = cfg.get("small_to_big", {}).get("enabled", True)

        results: List[Dict[str, Any]] = []

        if mode == "keyword":
            results = retrieve_keyword(query, chunks, kw_top)
            for r in results:
                ks = r.get("keyword_score", 0.0)
                r["score"] = ks
                r["score_breakdown"] = {"keyword_score": ks, "vector_score": None, "rrf_score": None, "rerank_score": None, "final_score": ks}
        elif mode == "vector":
            results = retrieve_vector(query, self._vector_store, self._embedding_model, vec_top)
            for r in results:
                vs = r.get("vector_score", 0.0)
                r["score"] = vs
                r["score_breakdown"] = {"keyword_score": None, "vector_score": vs, "rrf_score": None, "rerank_score": None, "final_score": vs}
        else:
            kw_results = retrieve_keyword(query, chunks, kw_top)
            try:
                vec_results = retrieve_vector(query, self._vector_store, self._embedding_model, vec_top)
            except VectorRetrievalError as e:
                _logger.error("Vector recall failed, continuing with keyword-only: %s", e)
                vec_results = []
            merged = merge_dedup(kw_results, vec_results, threshold=dedup_threshold)
            if merged:
                candidates = rrf_fusion(merged, k=rrf_k, top_n=rrf_n)
                domain_chunks = self._chunks_by_id.get(domain, {})
                if enable_ce and domain_chunks:
                    candidates = enrich_context(candidates, domain_chunks, small_to_big_enabled=small_to_big)
                enriched = [c.get("_enriched_text", c.get("content", "")) for c in candidates]
                try:
                    results = rerank_candidates(candidates, query, self._reranker_model, top_k_rerank, allow_rerank_fallback=allow_fallback, enriched_texts=enriched)
                except RerankerError:
                    raise

        for r in results:
            sb = r.setdefault("score_breakdown", {})
            sb.setdefault("keyword_score", r.get("keyword_score"))
            sb.setdefault("vector_score", r.get("vector_score"))
            sb.setdefault("rrf_score", r.get("rrf_score"))
            sb.setdefault("rerank_score", r.get("rerank_score"))
            sb.setdefault("final_score", r.get("score", 0.0))
            r.setdefault("retriever", mode)
        return results, {"profile": "hybrid_advanced"}

    # ── Eval ──

    def search_multi_mode(self, query: str, domain: str, top_k: int, modes: List[str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {"keyword": [], "vector": [], "hybrid": [], "debug": {}}
        for m in modes:
            sr = self.search(query, domain=domain, top_k=top_k, mode=m)
            result[m] = sr["results"]
        chunks = self._get_chunks(domain)
        result["debug"] = {"vector_available": self.vector_available, "chunk_count": len(chunks), "mode": self.mode}
        return result

    # ── 问答 ──

    def answer(self, question: str, domain: str = "bank_stmt", top_k: int = 5, with_citations: bool = True, return_retrieved: bool = False) -> Dict[str, Any]:
        t0 = time.perf_counter()
        sr = self.search(question, domain=domain, top_k=top_k, mode="hybrid")
        results = sr["results"]

        if not results:
            elapsed = (time.perf_counter() - t0) * 1000
            return {"answer": "知识库信息不足，无法回答该问题。", "citations": [], "retrieved": [], "retriever": sr["retriever"], "latency_ms": round(elapsed, 1)}

        citations = [c.model_dump() if hasattr(c, "model_dump") else c for c in build_citations(results)] if with_citations else []

        from service.models.answer_llm import generate_answer
        answer_text = generate_answer(question, results, self._llm_config)

        elapsed = (time.perf_counter() - t0) * 1000
        return {"answer": answer_text, "citations": citations, "retrieved": results if return_retrieved else [], "retriever": sr["retriever"], "latency_ms": round(elapsed, 1)}

    # ── 索引信息 ──

    def get_index_info(self, domain: str = "bank_stmt") -> Dict[str, Any]:
        chunks = self._get_chunks(domain)
        snap = self._domain_snapshots.get(domain, {})
        return {"domain": domain, "chunk_count": len(chunks), "mode": self.mode, "keyword_available": self.keyword_available, "vector_available": self.vector_available, "rerank_available": self.rerank_available, "manifest_version": snap.get("version")}

    def reindex_domain(self, domain: str) -> Dict[str, Any]:
        """执行完整索引构建 pipeline: load→parse→parent/child→neighbor→BM25→vector→manifest。"""
        knowledge_dir = self._knowledge_dirs.get(domain, "")
        if not knowledge_dir:
            raise ValueError(f"unknown domain: {domain}")

        from pathlib import Path
        from service.knowledge.markdown_parser import parse_markdown
        from service.knowledge.parent_child import build_parent_child_chunks
        from service.knowledge.neighbor import link_neighbors
        from service.knowledge.manifest import build_manifest, compute_knowledge_version
        from service.storage.manifest_store import save_manifest, next_version
        from service.core.config import get_config

        cfg = get_config()
        index_dir = cfg["paths"]["index_dir"]
        domain_cfg = self._get_domain_cfg(domain)
        directory = Path(knowledge_dir)

        all_parents: list = []
        all_children: list = []

        for md_file in sorted(directory.rglob("*.md")):
            if md_file.name.startswith("."):
                continue
            raw_text = md_file.read_text(encoding="utf-8")
            if not raw_text.strip():
                continue
            rel_path = str(md_file.relative_to(directory))
            parsed = parse_markdown(raw_text)
            if not parsed.content.strip():
                continue

            def _tk(text: str) -> int:
                if self._embedding_model and hasattr(self._embedding_model, "count_tokens"):
                    return self._embedding_model.count_tokens(text)
                return len(text)

            parents, children = build_parent_child_chunks(
                relative_path=rel_path, parsed=parsed,
                token_counter=_tk, embedding_model=self._embedding_model,
            )
            all_parents.extend(parents)
            all_children.extend(children)

        link_neighbors(all_children)

        # BM25 persist
        bm25_ns = domain_cfg.get("bm25_namespace", domain)
        try:
            from service.storage.bm25 import BM25Index
            bm25_idx = BM25Index(namespace=bm25_ns, index_dir=index_dir)
            bm25_idx.build([_chunk_to_dict(c) for c in all_children])
            bm25_idx.save()
        except Exception as e:
            _logger.warning("BM25 persist failed: %s", e)

        # Rebuild vector index
        if self._embedding_model and self._vector_store:
            try:
                child_dicts = [_chunk_to_dict(c) for c in all_children]
                texts = [c.get("content", "") for c in child_dicts]
                vectors = self._embedding_model.encode(texts)
                self._vector_store._chunks = []
                self._vector_store._vectors = []
                self._vector_store.add(child_dicts, vectors)
            except Exception as e:
                _logger.warning("Vector rebuild failed: %s", e)

        # Write manifest
        chunk_ver = "v3"
        know_ver = compute_knowledge_version(knowledge_dir)
        emb_id = cfg.get("embedding", {}).get("model_id")
        emb_rev = cfg.get("embedding", {}).get("revision", "")
        manifest = build_manifest(
            domain=domain, collection=domain_cfg.get("collection"),
            bm25_namespace=bm25_ns, knowledge_version=know_ver,
            chunk_version=chunk_ver, parent_chunks=all_parents,
            child_chunks=all_children, embedding_model_id=emb_id,
            embedding_revision=emb_rev,
            vector_size=cfg.get("embedding", {}).get("dim", 512),
            distance=cfg.get("qdrant", {}).get("distance", "Cosine"),
            version=next_version(index_dir, domain),
        )
        save_manifest(manifest, index_dir)

        # Populate chunks_by_id for context_enrich + citation
        all_chunks = all_parents + all_children
        self._chunks_by_id[domain] = {c.chunk_id: c for c in all_chunks if hasattr(c, "chunk_id")}

        self._chunk_cache.pop(domain, None)
        _logger.info("Reindex complete: domain=%s parents=%d children=%d v%d", domain, len(all_parents), len(all_children), manifest.version)
        return {"domain": domain, "parent_count": len(all_parents), "child_count": len(all_children), "knowledge_version": know_ver, "manifest_version": manifest.version}

    def reload_knowledge(self, domain: str) -> None:
        self._chunk_cache.pop(domain, None)
        _logger.info("Knowledge cache cleared for domain: %s", domain)


def _chunk_to_dict(chunk: Any) -> Dict[str, Any]:
    """Chunk Pydantic model → dict for downstream consumers."""
    if hasattr(chunk, "model_dump"):
        return chunk.model_dump()
    return dict(chunk)
