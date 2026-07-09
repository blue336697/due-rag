"""content_hash + chunk_id 稳定生成测试 (§13 单元测试)。"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.core.hashing import (
    compute_chunk_id, compute_content_hash,
    compute_manifest_content_hash, normalize_content,
)


def test_normalize_line_endings():
    result = normalize_content("line1\r\nline2\rline3")
    assert "\r" not in result
    assert result == "line1\nline2\nline3"


def test_normalize_trailing_spaces():
    assert normalize_content("hello   \nworld  ") == "hello\nworld"


def test_normalize_compress_blank_lines():
    assert normalize_content("a\n\n\n\n\nb") == "a\n\n\nb"


def test_normalize_trim():
    assert normalize_content("\n\n  hello  \n\n") == "hello"


def test_content_hash_deterministic():
    h1 = compute_content_hash("交易金额是银行流水中最重要的字段之一。")
    h2 = compute_content_hash("交易金额是银行流水中最重要的字段之一。")
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_content_hash_different():
    assert compute_content_hash("交易金额") != compute_content_hash("发生额")


def test_chunk_id_deterministic():
    ch = compute_content_hash("测试正文")
    cid1 = compute_chunk_id("字段定义/amount.md", ["字段定义", "交易金额"], 1, ch)
    cid2 = compute_chunk_id("字段定义/amount.md", ["字段定义", "交易金额"], 1, ch)
    assert cid1 == cid2


def test_chunk_id_collision_resistant():
    """JSON 序列化 heading_path 避免 source 边界碰撞。"""
    ch = compute_content_hash("x")
    cid1 = compute_chunk_id("a/b", ["c"], 1, ch)
    cid2 = compute_chunk_id("a", ["b/c"], 1, ch)
    assert cid1 != cid2


def test_manifest_content_hash_stable():
    pairs = [("sha256:ccc", "sha256:333"), ("sha256:aaa", "sha256:111"), ("sha256:bbb", "sha256:222")]
    h1 = compute_manifest_content_hash(pairs)
    h2 = compute_manifest_content_hash(list(reversed(pairs)))
    assert h1 == h2
