"""检索结果字段完整性和 top_k API 契约测试。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from service.retrieval.fusion import merge_dedup, rrf_fusion
from service.retrieval.service import RetrievalService
from service.storage.bm25 import keyword_recall


def _result(index: int, score_field: str, score: float) -> dict:
    return {
        "source": f"doc-{index}.md",
        "heading": f"标题 {index}",
        "heading_path": ["测试", f"标题 {index}"],
        "content": f"交易金额测试内容 {index}",
        "chunk_id": f"chunk-{index}",
        "parent_id": f"parent-{index}",
        "content_hash": f"hash-{index}",
        "metadata": {"index": index},
        score_field: score,
        "score": score,
    }


class KeywordTraceabilityTests(unittest.TestCase):
    def test_keyword_recall_preserves_trace_fields(self):
        chunk = _result(1, "seed_score", 1.0)
        result = keyword_recall("交易金额", [chunk], 5)[0]

        self.assertEqual(result["chunk_id"], "chunk-1")
        self.assertEqual(result["parent_id"], "parent-1")
        self.assertEqual(result["content_hash"], "hash-1")
        self.assertEqual(result["heading_path"], ["测试", "标题 1"])
        self.assertEqual(result["metadata"], {"index": 1})

    def test_hybrid_dedup_merges_trace_fields_and_both_scores(self):
        keyword = {
            "source": "doc.md",
            "content": "完全相同的交易金额定义",
            "keyword_score": 0.9,
        }
        vector = {
            "source": "doc.md",
            "heading_path": ["字段定义", "交易金额"],
            "content": "完全相同的交易金额定义",
            "chunk_id": "chunk-1",
            "parent_id": "parent-1",
            "content_hash": "hash-1",
            "metadata": {"category": "字段定义"},
            "vector_score": 0.8,
        }

        merged = merge_dedup([keyword], [vector])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["chunk_id"], "chunk-1")
        self.assertEqual(merged[0]["content_hash"], "hash-1")
        self.assertEqual(merged[0]["keyword_score"], 0.9)
        self.assertEqual(merged[0]["vector_score"], 0.8)

        fused = rrf_fusion(merged)
        self.assertEqual(fused[0]["keyword_rank"], 1)
        self.assertEqual(fused[0]["vector_rank"], 1)
        self.assertAlmostEqual(fused[0]["rrf_score"], round(2 / 61, 6))


class TopKContractTests(unittest.TestCase):
    def setUp(self):
        self.service = RetrievalService(
            knowledge_dirs={"bank_stmt": ""},
            domain_configs={"bank_stmt": {"pipeline_profile": "hybrid_advanced"}},
            vector_store=object(),
            embedding_model=object(),
            reranker_model=object(),
            retrieval_config={
                "top_k_keyword": 20,
                "top_k_vector": 40,
                "rrf_top_n": 10,
                "top_k_rerank": 5,
                "enable_context_enrich": False,
            },
            llm_config={},
        )

    def test_all_modes_honor_requested_top_k(self):
        keyword_results = [_result(i, "keyword_score", 1 - i / 100) for i in range(20)]
        vector_results = [_result(i + 100, "vector_score", 1 - i / 100) for i in range(40)]

        def fake_keyword(_query, _chunks, limit):
            return [dict(result) for result in keyword_results[:limit]]

        def fake_vector(_query, _store, _model, limit):
            return [dict(result) for result in vector_results[:limit]]

        def fake_rerank(candidates, _query, _model, limit, **_kwargs):
            return [dict(result, score=result.get("rrf_score", 0.0)) for result in candidates[:limit]]

        with (
            patch("service.retrieval.service.retrieve_keyword", side_effect=fake_keyword),
            patch("service.retrieval.service.retrieve_vector", side_effect=fake_vector),
            patch("service.retrieval.service.rerank_candidates", side_effect=fake_rerank) as rerank,
        ):
            for mode in ("keyword", "vector", "hybrid"):
                response = self.service.search("交易金额", top_k=5, mode=mode)
                self.assertEqual(len(response["results"]), 5, mode)
                for result in response["results"]:
                    citation = result["citations"][0]
                    self.assertTrue(citation["chunk_id"], mode)
                    self.assertTrue(citation["content_hash"], mode)

            response = self.service.search("交易金额", top_k=7, mode="hybrid")
            self.assertEqual(len(response["results"]), 7)
            self.assertEqual(rerank.call_args.args[3], 7)


if __name__ == "__main__":
    unittest.main()
