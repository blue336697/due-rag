"""IndexManifest 生成和校验。

按 RAG高级检索能力开发指南 §6.4:
  - domain 顶层 manifest，写入 indexes/manifests/<domain>.json
  - version 单调递增，每次 reindex 成功后自增 1
  - content_hash 基于 (chunk_id, content_hash) 排序行计算
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from service.schemas.rag import Chunk, IndexManifest


def compute_knowledge_version(
    knowledge_dir: str,
    additional_dirs: Optional[Sequence[Tuple[str, str]]] = None,
) -> str:
    """基于人工知识库及附加 managed 目录生成稳定 fingerprint。"""
    sources = [("", Path(knowledge_dir))]
    sources.extend((prefix.strip("/\\"), Path(path)) for prefix, path in (additional_dirs or []))
    if not any(directory.exists() for _, directory in sources):
        return "empty"

    hasher = hashlib.sha256()
    for prefix, directory in sources:
        if not directory.exists():
            continue
        for md_file in sorted(directory.rglob("*.md")):
            if md_file.name.startswith("."):
                continue
            rel = md_file.relative_to(directory).as_posix()
            source = f"{prefix}/{rel}" if prefix else rel
            hasher.update(source.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(md_file.read_bytes())
            hasher.update(b"\0")
    return hasher.hexdigest()[:16]


def build_manifest(
    domain: str,
    collection: Optional[str],
    bm25_namespace: str,
    knowledge_version: str,
    chunk_version: str,
    parent_chunks: List[Chunk],
    child_chunks: List[Chunk],
    embedding_model_id: Optional[str],
    embedding_revision: Optional[str],
    vector_size: Optional[int],
    distance: Optional[str],
    version: int = 1,
    external_version: Optional[str] = None,
) -> IndexManifest:
    """从 chunk 列表构建 IndexManifest。"""
    from service.core.hashing import compute_manifest_content_hash

    all_chunks = parent_chunks + child_chunks
    chunk_pairs = [(c.chunk_id, c.content_hash) for c in all_chunks]
    manifest_hash = compute_manifest_content_hash(chunk_pairs)

    return IndexManifest(
        version=version,
        domain=domain,
        collection=collection,
        bm25_namespace=bm25_namespace,
        knowledge_version=external_version or knowledge_version,
        chunk_version=chunk_version,
        embedding_model_id=embedding_model_id,
        embedding_revision=embedding_revision,
        vector_size=vector_size,
        distance=distance,
        chunk_count=len(all_chunks),
        parent_count=len(parent_chunks),
        child_count=len(child_chunks),
        content_hash=manifest_hash,
        created_at=datetime.now(),
    )
