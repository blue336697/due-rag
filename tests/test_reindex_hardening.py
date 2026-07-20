"""Reindex 加固测试；不依赖真实模型、Qdrant 或 Redis。"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from service.knowledge.markdown_parser import parse_markdown
from service.knowledge.semantic_chunker import semantic_chunk
from service.retrieval.service import ReindexError, RetrievalService
from service.reindex.jobs import ReindexJobManager
from service.schemas.rag import ReindexRequest
from service.storage.manifest_store import load_manifest
from service.storage.qdrant import InMemoryVectorStore


class FakeEmbedding:
    def count_tokens(self, text: str) -> int:
        return len(text)

    def encode(self, texts):
        return [[1.0, 0.0] if i % 2 == 0 else [0.0, 1.0] for i, _ in enumerate(texts)]


class FailingEmbedding(FakeEmbedding):
    def encode(self, texts):
        raise RuntimeError("embedding failed")


def _config(root: Path, index_dir: Path) -> dict:
    return {
        "paths": {"index_dir": str(index_dir)},
        "chunking": {
            "parent_max_tokens": 1200,
            "child_max_tokens": 350,
            "child_min_tokens": 80,
            "overlap_tokens": 40,
        },
        "embedding": {
            "model_id": "fake",
            "revision": "test",
            "dim": 2,
            "semantic_similarity_threshold": 0.62,
        },
        "qdrant": {"distance": "Cosine", "keep_versions": 2},
        "reindex": {"job_ttl_seconds": 60},
        "redis": {"host": "127.0.0.1", "port": 1, "db": 0, "password_env": "TEST_REDIS_PASSWORD"},
    }


class SemanticChunkerTests(unittest.TestCase):
    def test_overlap_uses_complete_previous_unit(self):
        chunks = semantic_chunk(
            "甲乙丙。丁戊己。庚辛壬。",
            token_counter=len,
            embedding_model=FakeEmbedding(),
            similarity_threshold=0.5,
            min_tokens=1,
            max_tokens=12,
            overlap_tokens=4,
        )
        self.assertEqual(chunks, ["甲乙丙。", "甲乙丙。丁戊己。", "丁戊己。庚辛壬。"])

    def test_table_is_an_atomic_unit(self):
        table = "|列A|列B|\n|---|---|\n|1|2|"
        text = f"前文。\n{table}\n后文。"
        start = text.index(table)
        chunks = semantic_chunk(
            text,
            token_counter=len,
            embedding_model=FakeEmbedding(),
            similarity_threshold=0.5,
            min_tokens=1,
            max_tokens=12,
            overlap_tokens=0,
            protected_ranges=[(start, start + len(table))],
        )
        self.assertTrue(any(table in chunk for chunk in chunks))

    def test_parser_detects_lists(self):
        parsed = parse_markdown("# 标题\n\n- 第一项\n- 第二项\n\n正文。")
        self.assertEqual(len(parsed.list_ranges), 1)


class ReindexTests(unittest.TestCase):
    def test_reindex_merges_read_only_source_and_writable_managed_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge"; root.mkdir()
            managed = Path(tmp) / "managed" / "bank_stmt"; managed.mkdir(parents=True)
            index_dir = Path(tmp) / "indexes"
            (root / "manual.md").write_text("# 人工知识\n\n## 规则\n\n人工维护的规则内容。", encoding="utf-8")
            managed_doc = managed / "doc_1.md"
            managed_doc.write_text("# 上传知识\n\n## 规则\n\n上传解析后的规则内容。", encoding="utf-8")
            service = RetrievalService(
                knowledge_dirs={"bank_stmt": str(root)},
                managed_knowledge_dirs={"bank_stmt": str(managed)},
                domain_configs={"bank_stmt": {"collection": "bank_stmt_knowledge", "bm25_namespace": "bank_stmt", "require_vector": True}},
                vector_store=InMemoryVectorStore(), embedding_model=FakeEmbedding(), reranker_model=None,
                retrieval_config={}, llm_config={},
            )
            with patch("service.core.config.get_config", return_value=_config(root, index_dir)):
                first = service.reindex_domain("bank_stmt", force=True)
                sources = {chunk["source"] for chunk in service._domain_snapshots["bank_stmt"]["chunks"]}
                self.assertTrue(any(source == "manual.md" for source in sources))
                self.assertTrue(any(source == "_managed/doc_1.md" for source in sources))
                managed_doc.write_text("# 上传知识\n\n## 规则\n\n上传内容已经更新。", encoding="utf-8")
                second = service.reindex_domain("bank_stmt", force=False)
                self.assertEqual(first["manifest_version"], 1)
                self.assertEqual(second["manifest_version"], 2)

    def test_reindex_is_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge"
            index_dir = Path(tmp) / "indexes"
            root.mkdir()
            source = root / "sample.md"
            source.write_text("# 示例\n\n## 定义\n\n这是新的知识材料。", encoding="utf-8")
            cfg = _config(root, index_dir)
            domains = {
                "bank_stmt": {
                    "enabled": True,
                    "collection": "bank_stmt_knowledge",
                    "bm25_namespace": "bank_stmt",
                    "require_vector": True,
                }
            }
            service = RetrievalService(
                knowledge_dirs={"bank_stmt": str(root)},
                domain_configs=domains,
                vector_store=InMemoryVectorStore(),
                embedding_model=FakeEmbedding(),
                reranker_model=None,
                retrieval_config={},
                llm_config={},
            )

            with patch("service.core.config.get_config", return_value=cfg):
                result = service.reindex_domain("bank_stmt", force=True)
                self.assertEqual(result["status"], "completed")
                manifest = load_manifest(str(index_dir), "bank_stmt")
                self.assertIsNotNone(manifest)
                self.assertEqual(manifest.version, 1)

                skipped = service.reindex_domain("bank_stmt", force=False)
                self.assertEqual(skipped["status"], "skipped")
                self.assertEqual(skipped["manifest_version"], 1)

                source.write_text("# 示例\n\n## 定义\n\n内容已经发生变化。", encoding="utf-8")
                failed_service = RetrievalService(
                    knowledge_dirs={"bank_stmt": str(root)},
                    domain_configs=domains,
                    vector_store=InMemoryVectorStore(),
                    embedding_model=FailingEmbedding(),
                    reranker_model=None,
                    retrieval_config={},
                    llm_config={},
                )
                with self.assertRaises(ReindexError):
                    failed_service.reindex_domain("bank_stmt", force=True)
                self.assertEqual(load_manifest(str(index_dir), "bank_stmt").version, 1)

    def test_collection_must_match_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge"
            root.mkdir()
            (root / "sample.md").write_text("# 示例\n\n正文。", encoding="utf-8")
            cfg = _config(root, Path(tmp) / "indexes")
            service = RetrievalService(
                knowledge_dirs={"bank_stmt": str(root)},
                domain_configs={"bank_stmt": {"collection": "expected", "require_vector": False}},
                vector_store=InMemoryVectorStore(),
                embedding_model=FakeEmbedding(),
                reranker_model=None,
                retrieval_config={},
                llm_config={},
            )
            with patch("service.core.config.get_config", return_value=cfg):
                with self.assertRaises(ReindexError):
                    service.reindex_domain("bank_stmt", collection="wrong", force=True)


class ReindexJobTests(unittest.TestCase):
    def test_job_runs_asynchronously_and_persists_status(self):
        cfg = {
            "reindex": {"job_ttl_seconds": 60},
            "redis": {"host": "127.0.0.1", "port": 1, "db": 0, "password_env": "TEST_REDIS_PASSWORD"},
        }

        class StubService:
            def reindex_domain(self, **kwargs):
                kwargs["progress_callback"]("embedding", 1, 2)
                return {"status": "completed", "child_count": 2}

        with patch("service.reindex.jobs.get_config", return_value=cfg):
            manager = ReindexJobManager()
        submitted = manager.submit(StubService(), ReindexRequest(domain="bank_stmt", force=True))
        self.assertEqual(submitted["status"], "queued")

        deadline = time.monotonic() + 2
        job = manager.get(submitted["job_id"])
        while job and job["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.01)
            job = manager.get(submitted["job_id"])

        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["progress"]["processed_chunks"], 2)


if __name__ == "__main__":
    unittest.main()
