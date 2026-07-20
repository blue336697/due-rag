"""异步文档解析任务。"""
from __future__ import annotations
import threading, uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Optional
from service.ingestion.service import IngestionService


class IngestionJobManager:
    def __init__(self, service: Optional[IngestionService] = None):
        self.service = service or IngestionService(); self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag-ingest")
        self.lock = threading.Lock(); self._scheduled: set[str] = set(); self.recover_pending()

    def submit(self, document_id: str) -> dict:
        job_id = f"ingest_{uuid.uuid4().hex[:12]}"; job = {"job_id": job_id, "document_id": document_id, "status": "queued", "stage": "queued", "error": None, "warnings": []}
        self.service.registry.put_job(job_id, job); self._schedule(job_id, document_id); return job

    def get(self, job_id: str) -> Optional[dict]: return self.service.registry.get_job(job_id)

    def recover_pending(self) -> int:
        recovered = 0
        for job in self.service.registry.list_jobs():
            if job.get("status") in {"queued", "running"}:
                job.update({"status": "queued", "stage": "recovered"}); self.service.registry.put_job(job["job_id"], job)
                self._schedule(job["job_id"], job["document_id"]); recovered += 1
        return recovered

    def _schedule(self, job_id: str, document_id: str) -> None:
        with self.lock:
            if job_id in self._scheduled: return
            self._scheduled.add(job_id)
        self.executor.submit(self._run, job_id, document_id)

    def _run(self, job_id: str, document_id: str) -> None:
        job = self.get(job_id) or {}; job.update({"status": "running", "stage": "extracting"}); self.service.registry.put_job(job_id, job)
        try:
            canonical = self.service.process(document_id); status = "awaiting_review" if canonical.quality.requires_review else "ready"
            job.update({"status": status, "stage": "done", "warnings": canonical.quality.warnings})
        except Exception as exc:
            job.update({"status": "failed", "stage": "failed", "error": str(exc)})
            record = self.service.registry.get_document(document_id)
            if record: record.update({"status": "failed", "error": str(exc)}); self.service.registry.put_document(document_id, record)
        self.service.registry.put_job(job_id, job)
        with self.lock: self._scheduled.discard(job_id)


@lru_cache(maxsize=1)
def get_ingestion_job_manager() -> IngestionJobManager: return IngestionJobManager()
