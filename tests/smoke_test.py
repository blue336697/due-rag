"""核心逻辑冒烟测试 — 不依赖 embedding/reranker 模型。"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.core.hashing import compute_chunk_id, compute_content_hash
from service.schemas.rag import Chunk, SearchResult, Citation
from service.knowledge.markdown_parser import parse_markdown, extract_heading_path
from service.knowledge.metadata import extract_metadata
from service.retrieval.fusion import merge_dedup, rrf_fusion
from service.retrieval.citation import build_citations
from service.storage import bm25


def test_markdown_parser():
    md = "---\ntitle: T\ncategory: C\n---\n\n# H1\n\n## H2\ncontent\n\n## H3\nmore"
    p = parse_markdown(md)
    assert p.frontmatter.get("title") == "T"
    assert len(p.heading_events) == 3
    print("  PASS: parser")


def test_heading_path():
    md = "# A\n\n## B\n\ncontent\n\n## C\n"
    p = parse_markdown(md)
    hp = extract_heading_path(p.heading_events, len(p.content) // 2)
    assert hp == ["A", "B"]
    print("  PASS: heading_path")


def test_metadata():
    md = "---\ntitle: X\ncategory: Y\n---\n\n## Z\ntext"
    p = parse_markdown(md)
    m = extract_metadata("Y/test.md", p, 30)
    assert m.title == "X" and m.category == "Y"
    print("  PASS: metadata")


def test_keyword():
    chunks = [{"source": "a.md", "heading": "H", "content": "交易金额是重要字段"}]
    r = bm25.keyword_recall("交易金额", chunks, 5)
    assert len(r) > 0
    print("  PASS: keyword")


def test_citation():
    r = [{"source": "a.md", "heading": "H", "heading_path": ["H"], "chunk_id": "sha256:aaa", "parent_id": None, "content_hash": "sha256:bbb", "content": "x"}]
    c = build_citations(r)
    assert c[0].index == 1 and c[0].chunk_id == "sha256:aaa"
    print("  PASS: citation")


def test_fusion():
    kw = [{"source": "a.md", "content": "交易金额是重要字段", "keyword_score": 0.9}]
    vec = [{"source": "a.md", "content": "交易金额是重要字段", "vector_score": 0.8}]
    m = merge_dedup(kw, vec)
    assert len(m) == 1  # Jaccard > 0.8 dedup
    f = rrf_fusion(m)
    assert "rrf_score" in f[0]
    print("  PASS: fusion")


def test_schemas():
    c = Chunk(chunk_id="sha256:a", source="x.md", heading_path=["A"], content="x", content_hash="sha256:b", chunk_type="child", ordinal=1)
    assert c.model_dump()["chunk_id"] == "sha256:a"
    sr = SearchResult(chunk_id="sha256:a", source="x.md", heading="A", heading_path=["A"], content="x", score=0.9, score_breakdown={"keyword_score": 0.8, "vector_score": None, "rrf_score": None, "rerank_score": None, "final_score": 0.8}, retriever="hybrid")
    assert sr.model_dump()["score_breakdown"]["vector_score"] is None
    print("  PASS: schemas")


if __name__ == "__main__":
    for t in [test_markdown_parser, test_heading_path, test_metadata, test_keyword, test_citation, test_fusion, test_schemas]:
        t()
    print("\nALL 7 SMOKE TESTS PASSED")
