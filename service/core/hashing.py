"""content_hash 规范化 + chunk_id 稳定生成。

按 RAG高级检索能力开发指南:
  §6.1 chunk_id  — sha256(source||JSON(heading_path)||ordinal||content_hash)
  §8.3.2 content_hash — sha256(normalized_content)，规范化规则固定

责任边界:
  - loader 保证 Chunk.content 已剥离 frontmatter
  - hasher 只接收 Chunk.content，不尝试识别或切除 frontmatter
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import List


def normalize_content(content: str) -> str:
    """规范化正文，用于 content_hash 计算。

    规则（§8.3.2）:
      1. 换行统一为 \\n
      2. 去除行尾空白
      3. 连续三个及以上空行压缩为两个空行（保留段落间距弱结构信号）
      4. 首尾 trim
      5. 保留中文标点、英文大小写、表格内容和代码块内容
    """
    # 1. 换行统一
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    # 2. 去除行尾空白
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # 3. 三个+ 连续空行 → 两个空行
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # 4. 首尾 trim
    text = text.strip()
    return text


def compute_content_hash(content: str) -> str:
    """计算规范化正文的 SHA256 content_hash。"""
    normalized = normalize_content(content)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_chunk_id(
    source: str,
    heading_path: List[str],
    ordinal: int,
    content_hash: str,
) -> str:
    """稳定生成 chunk_id。

    算法（§6.1）:
      payload = [source, JSON(heading_path), str(ordinal), content_hash]
      chunk_id = "sha256:" + sha256("||".join(payload))

    使用 JSON 序列化 heading_path（非 "/".join），分隔符固定为 "||"，
    避免 source="a/b", heading_path=["c"] 与 source="a", heading_path=["b/c"] 碰撞。
    """
    heading_json = json.dumps(heading_path, ensure_ascii=False, separators=(",", ":"))
    payload = [
        source,
        heading_json,
        str(ordinal),
        content_hash,
    ]
    joined = "||".join(payload)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_manifest_content_hash(chunk_pairs: List[tuple[str, str]]) -> str:
    """计算 IndexManifest.content_hash。

    输入为所有 chunk 的 (chunk_id, content_hash) 二元组。
    先按 chunk_id 字典序稳定排序，每行序列化为 chunk_id + \\t + content_hash，
    行之间用 \\n 连接。

    §6.4: content_hash = sha256(排序行.join("\\n"))
    """
    sorted_pairs = sorted(chunk_pairs, key=lambda p: p[0])
    lines = [f"{chunk_id}\t{content_hash}" for chunk_id, content_hash in sorted_pairs]
    joined = "\n".join(lines)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
