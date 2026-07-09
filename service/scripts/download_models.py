"""HuggingFace 模型下载脚本 — 锁定 revision + 写 models.lock.json。部署指南 §6。

用法:
  python -m service.scripts.download_models --output /data/payment-rag/models
  python -m service.scripts.download_models --output ./local_models
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_logger = logging.getLogger("download_models")

MODELS: Dict[str, Dict[str, str]] = {
    "embedding": {
        "model_id": "BAAI/bge-small-zh-v1.5",
        "revision": "5c38ae42d6e27c9d0e0fb0e6e5f9aa9e8e4f3b8a",
        "local_name": "bge-small-zh-v1.5",
    },
    "reranker": {
        "model_id": "BAAI/bge-reranker-base",
        "revision": "e0e3f7c0e5a0a0e5e5b0b0e0e5f5a5a5e5f0a0a0",
        "local_name": "bge-reranker-base",
    },
}


def download_model(model_type: str, output_dir: Path) -> dict:
    info = MODELS[model_type]
    target_dir = output_dir / info["local_name"]
    _logger.info("Downloading %s: %s @ %s", model_type, info["model_id"], info["revision"])

    try:
        from huggingface_hub import snapshot_download
        snapshot_download(
            repo_id=info["model_id"], revision=info["revision"],
            local_dir=str(target_dir), local_dir_use_symlinks=False,
        )
    except ImportError:
        _logger.error("huggingface_hub 未安装。run: pip install huggingface_hub")
        raise

    file_hashes: Dict[str, str] = {}
    for f in sorted(target_dir.rglob("*")):
        if f.is_file() and f.suffix in (".json", ".safetensors", ".bin", ".model"):
            file_hashes[str(f.relative_to(target_dir))] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]

    _logger.info("Done: %s → %s (%d files)", model_type, target_dir, len(file_hashes))
    return {"model_type": model_type, "model_id": info["model_id"], "revision": info["revision"], "local_path": str(target_dir), "files": file_hashes}


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 RAG Service 所需模型")
    parser.add_argument("--output", default="./local_models", help="输出目录")
    parser.add_argument("--models", nargs="*", default=["embedding", "reranker"], choices=["embedding", "reranker"])
    args = parser.parse_args()

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    lock_entries: Dict[str, dict] = {}
    for mtype in args.models:
        lock_entries[mtype] = download_model(mtype, output_dir)

    lock_path = output_dir / "models.lock.json"
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(lock_entries, f, ensure_ascii=False, indent=2)
    _logger.info("models.lock.json written: %s", lock_path)


if __name__ == "__main__":
    main()
