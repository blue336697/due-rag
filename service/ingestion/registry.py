"""原文件、规范文档和任务状态的文件型持久化注册表。"""
from __future__ import annotations

import json, os, tempfile, threading
from pathlib import Path
from typing import Any, Dict, Optional


class IngestionRegistry:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.raw_dir = self.root / "raw"
        self.normalized_dir = self.root / "normalized"
        self.jobs_dir = self.root / "jobs"
        self.records_dir = self.root / "documents"
        for path in (self.raw_dir, self.normalized_dir, self.jobs_dir, self.records_dir):
            path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def raw_path(self, document_id: str, extension: str) -> Path:
        directory = self.raw_dir / document_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"original{extension}"

    def save_raw(self, document_id: str, extension: str, content: bytes) -> Path:
        path = self.raw_path(document_id, extension)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".original.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content); f.flush(); os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        return path

    def canonical_path(self, document_id: str) -> Path:
        return self.normalized_dir / document_id / "document.json"

    def markdown_path(self, document_id: str) -> Path:
        return self.normalized_dir / document_id / "document.md"

    def put_document(self, document_id: str, data: Dict[str, Any]) -> None:
        self._write_json(self.records_dir / f"{document_id}.json", data)

    def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(self.records_dir / f"{document_id}.json")

    def put_job(self, job_id: str, data: Dict[str, Any]) -> None:
        self._write_json(self.jobs_dir / f"{job_id}.json", data)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(self.jobs_dir / f"{job_id}.json")

    def list_jobs(self) -> list[Dict[str, Any]]:
        jobs = []
        for path in self.jobs_dir.glob("*.json"):
            item = self._read_json(path)
            if item: jobs.append(item)
        return jobs

    def save_canonical(self, document_id: str, data: Dict[str, Any], markdown: str) -> None:
        self._write_json(self.canonical_path(document_id), data)
        self._write_text(self.markdown_path(document_id), markdown)

    def load_canonical(self, document_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(self.canonical_path(document_id))

    def find_by_hash(self, domain: str, source_hash: str) -> Optional[Dict[str, Any]]:
        for path in self.records_dir.glob("*.json"):
            item = self._read_json(path)
            if item and item.get("domain") == domain and item.get("source_hash") == source_hash and item.get("status") != "deleted":
                return item
        return None

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists(): return None
        with self._lock, open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        self._write_text(path, json.dumps(data, ensure_ascii=False, indent=2, default=str))

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text); f.flush(); os.fsync(f.fileno())
                os.replace(temp, path)
            finally:
                if os.path.exists(temp): os.unlink(temp)
