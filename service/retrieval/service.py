"""检索服务 — RAG Service 内部统一检索入口 + 问答。

按 RAG高级检索能力开发指南 §8:
  - Domain pipeline profile 分派: hybrid_advanced / keyword_simple
  - score_breakdown + citation builder 集成
  - context_enrich + rerank 按 domain 配置执行

调用方: api/rag.py, api/admin.py, api/health.py, service/main.py
"""
from __future__ import annotations

import logging
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
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


class ReindexError(RuntimeError):
    """带阶段信息的索引重建错误。"""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"{stage}: {message}")


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
        managed_knowledge_dirs: Optional[Dict[str, str]] = None,
    ):
        self._knowledge_dirs = knowledge_dirs
        self._managed_knowledge_dirs = managed_knowledge_dirs or {}
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
        snapshot = self._domain_snapshots.get(domain)
        if snapshot is not None:
            return snapshot.get("chunks", [])
        if domain not in self._chunk_cache:
            knowledge_dir = self._knowledge_dirs.get(domain, "")
            chunks = load_knowledge_chunks(knowledge_dir) if knowledge_dir and Path(knowledge_dir).exists() else []
            managed_dir = self._managed_knowledge_dirs.get(domain, "")
            if managed_dir and Path(managed_dir).exists():
                chunks.extend(load_knowledge_chunks(managed_dir, source_prefix="_managed"))
            self._chunk_cache[domain] = chunks
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
        snapshot = self._domain_snapshots.get(domain, {})
        chunks = snapshot.get("chunks") or self._get_chunks(domain)
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
        snapshot = self._domain_snapshots.get(domain, {})
        chunks = snapshot.get("chunks") or self._get_chunks(domain)
        vector_store = snapshot.get("vector_store", self._vector_store)
        domain_chunks = snapshot.get("chunks_by_id", self._chunks_by_id.get(domain, {}))

        kw_top = max(top_k, cfg.get("top_k_keyword", cfg.get("keyword_recall_top", 20)))
        vec_top = max(top_k, cfg.get("top_k_vector", cfg.get("vector_recall_top", 40)))
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
            results = retrieve_vector(query, vector_store, self._embedding_model, vec_top)
            for r in results:
                vs = r.get("vector_score", 0.0)
                r["score"] = vs
                r["score_breakdown"] = {"keyword_score": None, "vector_score": vs, "rrf_score": None, "rerank_score": None, "final_score": vs}
        else:
            kw_results = retrieve_keyword(query, chunks, kw_top)
            try:
                vec_results = retrieve_vector(query, vector_store, self._embedding_model, vec_top)
            except VectorRetrievalError as e:
                _logger.error("Vector recall failed, continuing with keyword-only: %s", e)
                vec_results = []
            merged = merge_dedup(kw_results, vec_results, threshold=dedup_threshold)
            if merged:
                candidates = rrf_fusion(merged, k=rrf_k, top_n=max(top_k, rrf_n))
                if enable_ce and domain_chunks:
                    candidates = enrich_context(candidates, domain_chunks, small_to_big_enabled=small_to_big)
                enriched = [c.get("_enriched_text", c.get("content", "")) for c in candidates]
                try:
                    rerank_limit = max(top_k, top_k_rerank)
                    results = rerank_candidates(candidates, query, self._reranker_model, rerank_limit, allow_rerank_fallback=allow_fallback, enriched_texts=enriched)
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
        return results[:top_k], {"profile": "hybrid_advanced"}

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
        vector_store = snap.get("vector_store", self._vector_store)
        vector_available = vector_store is not None and self._embedding_model is not None
        return {"domain": domain, "chunk_count": len(chunks), "mode": "hybrid" if vector_available else "keyword", "keyword_available": self.keyword_available, "vector_available": vector_available, "rerank_available": self.rerank_available, "manifest_version": snap.get("version")}

    def load_persisted_indexes(self) -> None:
        """冷启动时从 Manifest、Chunk snapshot 和 BM25 恢复运行期快照。"""
        from service.core.config import get_config
        from service.storage.bm25 import BM25Index
        from service.storage.manifest_store import load_manifest

        cfg = get_config()
        index_dir = cfg["paths"]["index_dir"]
        for domain in self._knowledge_dirs:
            manifest = load_manifest(index_dir, domain)
            if manifest is None:
                continue
            all_chunk_dicts = _load_chunks_snapshot(index_dir, domain, manifest.version)
            bm25_index = BM25Index(namespace=manifest.bm25_namespace, index_dir=index_dir)
            bm25_loaded = bm25_index.load()
            if not all_chunk_dicts and bm25_loaded:
                all_chunk_dicts = list(bm25_index.chunks)
            if not all_chunk_dicts:
                continue

            chunks_by_id: Dict[str, Chunk] = {}
            child_dicts: List[Dict[str, Any]] = []
            for item in all_chunk_dicts:
                try:
                    chunk = Chunk(**item)
                except Exception:
                    continue
                chunks_by_id[chunk.chunk_id] = chunk
                if chunk.chunk_type == "child":
                    child_dicts.append(chunk.model_dump())

            if not child_dicts:
                continue
            snapshot = {
                "version": manifest.version,
                "manifest": manifest,
                "chunks": child_dicts,
                "chunks_by_id": chunks_by_id,
                "bm25_index": bm25_index if bm25_loaded else None,
                "vector_store": self._vector_store,
            }
            self._domain_snapshots = {**self._domain_snapshots, domain: snapshot}
            self._chunk_cache[domain] = child_dicts
            self._chunks_by_id[domain] = chunks_by_id
            _logger.info("Persisted index loaded: domain=%s v%d children=%d", domain, manifest.version, len(child_dicts))

    def reindex_domain(
        self,
        domain: str,
        collection: Optional[str] = None,
        force: bool = False,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """构建、验证并发布一个完整的新索引 generation。"""
        knowledge_dir = self._knowledge_dirs.get(domain, "")
        if not knowledge_dir:
            raise ValueError(f"unknown domain: {domain}")

        from service.knowledge.markdown_parser import parse_markdown
        from service.knowledge.parent_child import build_parent_child_chunks
        from service.knowledge.neighbor import link_neighbors
        from service.knowledge.manifest import build_manifest, compute_knowledge_version
        from service.storage.manifest_store import load_manifest, save_manifest, next_version
        from service.core.config import get_config

        cfg = get_config()
        index_dir = cfg["paths"]["index_dir"]
        domain_cfg = self._get_domain_cfg(domain)
        directory = Path(knowledge_dir)
        managed_directory = Path(self._managed_knowledge_dirs.get(domain, "")) if self._managed_knowledge_dirs.get(domain) else None
        if not directory.exists() and not (managed_directory and managed_directory.exists()):
            raise ReindexError("validate", f"knowledge directories not found: source={directory}, managed={managed_directory}")

        configured_collection = domain_cfg.get("collection")
        if collection and configured_collection and collection != configured_collection:
            raise ReindexError(
                "validate",
                f"collection {collection!r} does not match domain configuration {configured_collection!r}",
            )

        additional_dirs = [("_managed", str(managed_directory))] if managed_directory and managed_directory.exists() else []
        know_ver = compute_knowledge_version(knowledge_dir, additional_dirs=additional_dirs)
        chunk_ver = _chunk_pipeline_version(cfg)
        current_manifest = load_manifest(index_dir, domain)
        manifest_compatible = bool(
            current_manifest
            and current_manifest.knowledge_version == know_ver
            and current_manifest.chunk_version == chunk_ver
            and current_manifest.embedding_model_id == cfg["embedding"].get("model_id")
            and (current_manifest.embedding_revision or "") == (cfg["embedding"].get("revision") or "")
            and current_manifest.vector_size == int(cfg["embedding"].get("dim", 512))
            and (current_manifest.distance or "").lower() == str(cfg["qdrant"].get("distance", "Cosine")).lower()
            and (
                not configured_collection
                or not current_manifest.collection
                or current_manifest.collection == configured_collection
                or current_manifest.collection.startswith(f"{configured_collection}_v")
            )
        )
        if manifest_compatible and not force:
            return {
                "status": "skipped", "domain": domain,
                "parent_count": current_manifest.parent_count,
                "child_count": current_manifest.child_count,
                "knowledge_version": know_ver,
                "manifest_version": current_manifest.version,
            }

        version = next_version(index_dir, domain)
        _report_progress(progress_callback, "parsing", 0, 0)

        all_parents: list = []
        all_children: list = []
        markdown_files: list[tuple[str, Path, Path]] = []
        if directory.exists():
            markdown_files.extend(("", directory, path) for path in sorted(directory.rglob("*.md")) if not path.name.startswith("."))
        if managed_directory and managed_directory.exists():
            markdown_files.extend(("_managed", managed_directory, path) for path in sorted(managed_directory.rglob("*.md")) if not path.name.startswith("."))
        markdown_files.sort(key=lambda item: f"{item[0]}/{item[2].relative_to(item[1]).as_posix()}")

        for file_index, (source_prefix, source_root, md_file) in enumerate(markdown_files, start=1):
            raw_text = md_file.read_text(encoding="utf-8")
            if not raw_text.strip():
                continue
            rel_path = md_file.relative_to(source_root).as_posix()
            if source_prefix:
                rel_path = f"{source_prefix}/{rel_path}"
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
                parent_max_tokens=int(cfg["chunking"].get("parent_max_tokens", 1200)),
                child_max_tokens=int(cfg["chunking"].get("child_max_tokens", 350)),
                child_min_tokens=int(cfg["chunking"].get("child_min_tokens", 80)),
                overlap_tokens=int(cfg["chunking"].get("overlap_tokens", 40)),
                similarity_threshold=float(cfg["embedding"].get("semantic_similarity_threshold", 0.62)),
                chunk_version=chunk_ver,
            )
            all_parents.extend(parents)
            all_children.extend(children)
            _report_progress(progress_callback, "parsing", file_index, len(markdown_files))

        link_neighbors(all_children)
        if not all_parents or not all_children:
            raise ReindexError("validate", "knowledge base produced zero parent or child chunks")

        child_dicts = [_chunk_to_dict(c) for c in all_children]
        all_chunk_dicts = [_chunk_to_dict(c) for c in all_parents + all_children]

        # 构建版本化 BM25，在线版本在发布前不受影响。
        bm25_base_ns = domain_cfg.get("bm25_namespace", domain)
        bm25_ns = f"{bm25_base_ns}/v{version}"
        _report_progress(progress_callback, "bm25", 0, len(all_children))
        try:
            from service.storage.bm25 import BM25Index
            bm25_idx = BM25Index(namespace=bm25_ns, index_dir=index_dir)
            bm25_idx.build(child_dicts, build_hash=know_ver)
            bm25_idx.save()
            verifier = BM25Index(namespace=bm25_ns, index_dir=index_dir)
            if not verifier.load() or verifier.chunk_count != len(all_children):
                raise RuntimeError("BM25 reload/count validation failed")
        except Exception as e:
            raise ReindexError("bm25", str(e)) from e
        _report_progress(progress_callback, "bm25", len(all_children), len(all_children))

        # 构建版本化向量索引。
        new_vector_store = self._vector_store
        published_collection = configured_collection
        require_vector = bool(domain_cfg.get("require_vector", False))
        if require_vector and (self._embedding_model is None or self._vector_store is None):
            raise ReindexError("vector", "required embedding model or vector store is unavailable")
        if require_vector:
            _report_progress(progress_callback, "embedding", 0, len(all_children))
            try:
                texts = [c.get("embedding_text") or c.get("content", "") for c in child_dicts]
                vectors = self._embedding_model.encode(texts)
                if len(vectors) != len(child_dicts):
                    raise RuntimeError(f"embedding count mismatch: {len(vectors)} != {len(child_dicts)}")
                expected_dim = int(cfg["embedding"].get("dim", 512))
                if vectors and len(vectors[0]) != expected_dim:
                    raise RuntimeError(f"embedding dimension mismatch: {len(vectors[0])} != {expected_dim}")

                if hasattr(self._vector_store, "with_collection"):
                    base_collection = configured_collection or f"{domain}_knowledge"
                    published_collection = f"{base_collection}_v{version}"
                    new_vector_store = self._vector_store.with_collection(published_collection)
                    new_vector_store.recreate_collection(
                        vector_size=expected_dim,
                        distance=cfg["qdrant"].get("distance", "Cosine"),
                    )
                    new_vector_store.create_payload_indexes()
                    new_vector_store.upsert(child_dicts, vectors)
                else:
                    from service.storage.qdrant import InMemoryVectorStore
                    new_vector_store = InMemoryVectorStore()
                    new_vector_store.add(child_dicts, vectors)
                if new_vector_store.count() != len(child_dicts):
                    raise RuntimeError(f"vector count mismatch: {new_vector_store.count()} != {len(child_dicts)}")
            except Exception as e:
                _discard_staging_generation(
                    new_vector_store, self._vector_store, index_dir, bm25_ns, domain, version,
                )
                raise ReindexError("vector", str(e)) from e
            _report_progress(progress_callback, "embedding", len(all_children), len(all_children))

        # 先持久化完整 chunk 快照，再原子发布 Manifest。
        try:
            _save_chunks_snapshot(index_dir, domain, version, all_chunk_dicts)
            emb_id = cfg.get("embedding", {}).get("model_id")
            emb_rev = cfg.get("embedding", {}).get("revision", "")
            manifest = build_manifest(
                domain=domain, collection=published_collection,
                bm25_namespace=bm25_ns, knowledge_version=know_ver,
                chunk_version=chunk_ver, parent_chunks=all_parents,
                child_chunks=all_children, embedding_model_id=emb_id,
                embedding_revision=emb_rev,
                vector_size=cfg.get("embedding", {}).get("dim", 512),
                distance=cfg.get("qdrant", {}).get("distance", "Cosine"),
                version=version,
            )
            _report_progress(progress_callback, "publishing", 0, len(all_children))
            save_manifest(manifest, index_dir)
        except Exception as e:
            _discard_staging_generation(
                new_vector_store, self._vector_store, index_dir, bm25_ns, domain, version,
            )
            raise ReindexError("publishing", str(e)) from e

        all_chunks = all_parents + all_children
        chunks_by_id = {c.chunk_id: c for c in all_chunks if hasattr(c, "chunk_id")}
        snapshot = {
            "version": manifest.version,
            "manifest": manifest,
            "chunks": child_dicts,
            "chunks_by_id": chunks_by_id,
            "bm25_index": bm25_idx,
            "vector_store": new_vector_store,
        }
        # 单次字典引用替换：在线请求只会看到旧快照或新快照。
        self._domain_snapshots = {**self._domain_snapshots, domain: snapshot}
        self._chunk_cache[domain] = child_dicts
        self._chunks_by_id[domain] = chunks_by_id
        if require_vector:
            self._vector_store = new_vector_store
        _report_progress(progress_callback, "publishing", len(all_children), len(all_children))

        # 发布成功后清理旧版本；清理失败不回滚已发布版本。
        try:
            keep = int(cfg["qdrant"].get("keep_versions", 2))
            if require_vector and hasattr(new_vector_store, "cleanup_versioned_collections"):
                new_vector_store.cleanup_versioned_collections(
                    configured_collection or f"{domain}_knowledge", keep, published_collection or "",
                )
            _cleanup_local_versions(index_dir, domain, bm25_base_ns, keep, version)
        except Exception:
            _logger.warning("Old index cleanup failed: domain=%s", domain, exc_info=True)

        _logger.info("Reindex complete: domain=%s parents=%d children=%d v%d", domain, len(all_parents), len(all_children), manifest.version)
        return {"status": "completed", "domain": domain, "collection": published_collection, "parent_count": len(all_parents), "child_count": len(all_children), "knowledge_version": know_ver, "manifest_version": manifest.version}

    def reload_knowledge(self, domain: str) -> None:
        self._chunk_cache.pop(domain, None)
        _logger.info("Knowledge cache cleared for domain: %s", domain)


def _chunk_to_dict(chunk: Any) -> Dict[str, Any]:
    """Chunk Pydantic model → dict for downstream consumers."""
    if hasattr(chunk, "model_dump"):
        return chunk.model_dump()
    return dict(chunk)


def _report_progress(callback: Any, stage: str, processed: int, total: int) -> None:
    if callback is not None:
        callback(stage, processed, total)


def _chunk_pipeline_version(cfg: Dict[str, Any]) -> str:
    payload = {
        "algorithm": "v3",
        "chunking": cfg.get("chunking", {}),
        "semantic_similarity_threshold": cfg.get("embedding", {}).get("semantic_similarity_threshold", 0.62),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"v3:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:12]}"


def _chunks_snapshot_path(index_dir: str, domain: str, version: int) -> Path:
    return Path(index_dir) / "chunks" / domain / f"v{version}.json"


def _save_chunks_snapshot(index_dir: str, domain: str, version: int, chunks: List[Dict[str, Any]]) -> None:
    path = _chunks_snapshot_path(index_dir, domain, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as f:
            temp_path = f.name
            json.dump(chunks, f, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _load_chunks_snapshot(index_dir: str, domain: str, version: int) -> List[Dict[str, Any]]:
    path = _chunks_snapshot_path(index_dir, domain, version)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        _logger.warning("Chunk snapshot load failed: %s", path, exc_info=True)
        return []


def _cleanup_local_versions(index_dir: str, domain: str, bm25_base_ns: str, keep: int, active_version: int) -> None:
    keep = max(1, keep)
    versions_to_keep = set(range(max(1, active_version - keep + 1), active_version + 1))
    chunks_dir = Path(index_dir) / "chunks" / domain
    if chunks_dir.exists():
        for path in chunks_dir.glob("v*.json"):
            try:
                version = int(path.stem[1:])
            except ValueError:
                continue
            if version not in versions_to_keep:
                path.unlink()

    bm25_dir = Path(index_dir) / "bm25" / bm25_base_ns
    if bm25_dir.exists():
        for path in bm25_dir.glob("v*"):
            if not path.is_dir():
                continue
            try:
                version = int(path.name[1:])
            except ValueError:
                continue
            if version not in versions_to_keep:
                shutil.rmtree(path)


def _discard_staging_generation(
    candidate_store: Any,
    live_store: Any,
    index_dir: str,
    bm25_namespace: str,
    domain: str,
    version: int,
) -> None:
    """尽力删除失败 generation；清理错误不覆盖原始失败原因。"""
    try:
        if candidate_store is not live_store and hasattr(candidate_store, "delete_collection"):
            candidate_store.delete_collection()
    except Exception:
        _logger.warning("Failed candidate Qdrant cleanup", exc_info=True)
    try:
        bm25_dir = Path(index_dir) / "bm25" / bm25_namespace
        if bm25_dir.exists():
            shutil.rmtree(bm25_dir)
        chunk_path = _chunks_snapshot_path(index_dir, domain, version)
        if chunk_path.exists():
            chunk_path.unlink()
    except Exception:
        _logger.warning("Failed candidate local artifact cleanup", exc_info=True)
