"""IndexManifest 持久化存储。

按 RAG高级检索能力开发指南 §6.4:
  写入路径: indexes/manifests/<domain>.json
  本地开发: backend/rag-service/.local/indexes/manifests/<domain>.json
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from service.schemas.rag import IndexManifest

_logger = logging.getLogger(__name__)


def _manifest_path(index_dir: str, domain: str) -> Path:
    return Path(index_dir) / "manifests" / f"{domain}.json"


def save_manifest(manifest: IndexManifest, index_dir: str) -> None:
    """原子写入 IndexManifest；失败时保留旧版本。"""
    filepath = _manifest_path(index_dir, manifest.domain)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    data = manifest.model_dump(mode="json")
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=filepath.parent,
            prefix=f".{filepath.name}.", suffix=".tmp", delete=False,
        ) as f:
            temp_path = f.name
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, filepath)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    _logger.info("Manifest saved: domain=%s v%d chunks=%d", manifest.domain, manifest.version, manifest.chunk_count)


def load_manifest(index_dir: str, domain: str) -> Optional[IndexManifest]:
    """从磁盘加载 IndexManifest。不存在时返回 None。"""
    filepath = _manifest_path(index_dir, domain)
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return IndexManifest(**json.load(f))
    except Exception:
        _logger.warning("Failed to load manifest: %s", filepath, exc_info=True)
        return None


def next_version(index_dir: str, domain: str) -> int:
    """获取下一个版本号。无 manifest 时返回 1。"""
    existing = load_manifest(index_dir, domain)
    return existing.version + 1 if existing else 1
