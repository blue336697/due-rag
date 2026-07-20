"""异步 reindex job：线程执行，Redis 持久化状态，内存模式用于本地回退。"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Dict, Optional

from service.core.config import get_config
from service.retrieval.service import ReindexError
from service.schemas.rag import ReindexRequest

_logger = logging.getLogger(__name__)


class ReindexJobStore:
    def __init__(self) -> None:
        cfg = get_config()
        self._ttl = int(cfg.get("reindex", {}).get("job_ttl_seconds", 86400))
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._redis: Any = None
        redis_cfg = cfg.get("redis", {})
        try:
            import redis

            if redis_cfg.get("url"):
                client = redis.Redis.from_url(redis_cfg["url"], decode_responses=True)
            else:
                import os
                password = os.getenv(redis_cfg.get("password_env", "REDIS_PASSWORD"), "") or None
                client = redis.Redis(
                    host=redis_cfg.get("host", "localhost"),
                    port=redis_cfg.get("port", 6379),
                    db=redis_cfg.get("db", 0),
                    password=password,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
            client.ping()
            self._redis = client
            _logger.info("Reindex job state uses Redis")
        except Exception as exc:
            _logger.warning("Redis unavailable; reindex job state uses process memory: %s", exc)

    @staticmethod
    def _key(job_id: str) -> str:
        return f"rag:reindex:job:{job_id}"

    def put(self, job: Dict[str, Any]) -> None:
        payload = json.dumps(job, ensure_ascii=False, default=str)
        if self._redis is not None:
            self._redis.set(self._key(job["job_id"]), payload, ex=self._ttl)
            return
        with self._lock:
            self._memory[job["job_id"]] = json.loads(payload)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self._redis is not None:
            payload = self._redis.get(self._key(job_id))
            return json.loads(payload) if payload else None
        with self._lock:
            job = self._memory.get(job_id)
            return dict(job) if job else None

    def update(self, job_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            job.update(changes)
            self.put(job)
            return job


class ReindexJobManager:
    def __init__(self) -> None:
        self._store = ReindexJobStore()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-reindex")
        self._domain_locks: Dict[str, threading.Lock] = {}
        self._domain_locks_guard = threading.Lock()

    def submit(self, service: Any, request: ReindexRequest) -> Dict[str, Any]:
        job_id = f"rag-reindex-{uuid.uuid4().hex[:8]}"
        job = {
            "job_id": job_id,
            "status": "queued",
            "domain": request.domain,
            "collection": request.collection,
            "force": request.force,
            "progress": {"stage": "queued", "processed_chunks": 0, "total_chunks": 0},
            "result": {},
            "error": None,
        }
        self._store.put(job)
        self._executor.submit(self._run, service, request, job_id)
        return job

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(job_id)

    def _get_domain_lock(self, domain: str) -> threading.Lock:
        with self._domain_locks_guard:
            return self._domain_locks.setdefault(domain, threading.Lock())

    def _run(self, service: Any, request: ReindexRequest, job_id: str) -> None:
        lock = self._get_domain_lock(request.domain)
        with lock:
            self._store.update(
                job_id,
                status="running",
                progress={"stage": "starting", "processed_chunks": 0, "total_chunks": 0},
            )

            def progress(stage: str, processed: int, total: int) -> None:
                self._store.update(
                    job_id,
                    progress={"stage": stage, "processed_chunks": processed, "total_chunks": total},
                )

            try:
                result = service.reindex_domain(
                    domain=request.domain,
                    collection=request.collection,
                    force=request.force,
                    progress_callback=progress,
                )
                final_status = "skipped" if result.get("status") == "skipped" else "completed"
                total = int(result.get("child_count", 0))
                self._store.update(
                    job_id,
                    status=final_status,
                    progress={"stage": "done", "processed_chunks": total, "total_chunks": total},
                    result=result,
                    error=None,
                )
            except ReindexError as exc:
                _logger.error("Reindex failed: job=%s stage=%s error=%s", job_id, exc.stage, exc)
                self._store.update(
                    job_id,
                    status="failed",
                    progress={"stage": exc.stage, "processed_chunks": 0, "total_chunks": 0},
                    error=str(exc),
                )
            except Exception as exc:
                _logger.exception("Unexpected reindex failure: job=%s", job_id)
                self._store.update(
                    job_id,
                    status="failed",
                    progress={"stage": "unexpected", "processed_chunks": 0, "total_chunks": 0},
                    error=str(exc),
                )


@lru_cache(maxsize=1)
def get_reindex_job_manager() -> ReindexJobManager:
    return ReindexJobManager()
