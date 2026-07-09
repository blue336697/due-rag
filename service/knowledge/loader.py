"""知识库文档加载与分块 — 旧版兼容路径。

chunk_id 使用 SHA256 稳定生成，不再用 rel_path#i 旧格式。
新代码应通过 reindex_domain() 使用 parent_child.py pipeline。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, List

from service.core.hashing import compute_chunk_id, compute_content_hash

CHUNK_VERSION = "v3"

_logger = logging.getLogger(__name__)


def content_fingerprint(directory: Path) -> str:
    """计算知识库目录内容的 SHA256 指纹。"""
    hasher = hashlib.sha256()
    for md_file in sorted(directory.rglob("*.md")):
        hasher.update(str(md_file.relative_to(directory)).encode())
        hasher.update(md_file.read_bytes())
    return hasher.hexdigest()[:16]


def load_knowledge_chunks(knowledge_dir: str) -> List[Dict[str, object]]:
    """加载知识库目录下所有 .md 文件，按二级标题切块。"""
    directory = Path(knowledge_dir)
    if not directory.exists():
        raise FileNotFoundError(f"知识库目录不存在: {knowledge_dir}")

    fingerprint = content_fingerprint(directory)
    chunks: List[Dict[str, object]] = []

    for md_file in sorted(directory.rglob("*.md")):
        filename = md_file.name
        if filename.startswith("."):
            continue
        text = md_file.read_text(encoding="utf-8")
        if not text.strip():
            continue

        rel_path = str(md_file.relative_to(directory))
        file_chunks = _split_markdown(text)
        for i, chunk in enumerate(file_chunks):
            heading = str(chunk.get("heading", ""))
            content = str(chunk.get("content", ""))
            content_hash = compute_content_hash(content)
            heading_path = [heading] if heading else []
            chunk_id = compute_chunk_id(
                source=rel_path, heading_path=heading_path,
                ordinal=i + 1, content_hash=content_hash,
            )
            chunk["chunk_id"] = chunk_id
            chunk["id"] = chunk_id
            chunk["source"] = rel_path
            chunk["heading_path"] = heading_path
            chunk["content_hash"] = content_hash
            chunk["chunk_version"] = CHUNK_VERSION
            chunk["content_fingerprint"] = fingerprint
            chunk["chunk_index"] = i
            chunks.append(chunk)

    _logger.info(
        "知识库加载完成: dir=%s, files=%d, chunks=%d, fingerprint=%s",
        knowledge_dir, len({c["source"] for c in chunks}), len(chunks), fingerprint,
    )
    return chunks


def _split_markdown(text: str) -> List[Dict[str, object]]:
    """按二级标题切分 Markdown 文本。"""
    import re
    heading_re = re.compile(r"^## (.+)$", re.MULTILINE)
    headings: List[tuple[int, str]] = [(m.start(), m.group(1)) for m in heading_re.finditer(text)]

    if not headings:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        return [{"heading": "", "content": p} for p in paragraphs]

    chunks: List[Dict[str, object]] = []
    for i, (pos, heading) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        chunks.append({"heading": heading, "content": text[pos:end].strip()})
    return chunks


def get_index_metadata(
    knowledge_dir: str,
    embedding_model: str,
    embedding_dim: int,
) -> Dict[str, object]:
    """生成索引元数据，用于持久化和校验。"""
    fingerprint = content_fingerprint(Path(knowledge_dir))
    return {
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "chunk_version": CHUNK_VERSION,
        "content_fingerprint": fingerprint,
        "knowledge_dir": str(knowledge_dir),
    }
