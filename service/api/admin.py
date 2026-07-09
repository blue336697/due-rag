"""管理端点 — reindex 异步任务。

POST /admin/reindex        — 提交异步重建任务
GET  /admin/reindex/{job_id} — 查询任务状态
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Request

from service.schemas.rag import (
    ReindexProgress,
    ReindexRequest,
    ReindexResponse,
    ReindexStatusResponse,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# 简易内存 job store（生产建议改用 Redis）
_jobs: Dict[str, Dict[str, Any]] = {}


@router.post("/reindex", response_model=ReindexResponse)
def reindex(request: Request, body: ReindexRequest) -> ReindexResponse:
    job_id = f"rag-reindex-{uuid.uuid4().hex[:8]}"
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": {"stage": "queued", "processed_chunks": 0, "total_chunks": 0},
        "collection": body.collection,
        "domain": body.domain,
    }

    # 清除缓存，下次检索时重新加载（简化实现，不做真正的异步后台重建）
    svc = request.app.state.retrieval_service
    try:
        reindex_result = svc.reindex_domain(body.domain)
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["progress"]["stage"] = "done"
        _jobs[job_id]["progress"]["total_chunks"] = reindex_result.get("child_count", 0)
        _jobs[job_id]["progress"]["processed_chunks"] = reindex_result.get("child_count", 0)
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["progress"]["stage"] = f"error: {e}"

    return ReindexResponse(
        job_id=job_id,
        status=_jobs[job_id]["status"],
        status_url=f"/admin/reindex/{job_id}",
    )


@router.get("/reindex/{job_id}", response_model=ReindexStatusResponse)
def reindex_status(job_id: str) -> ReindexStatusResponse:
    job = _jobs.get(job_id)
    if not job:
        return ReindexStatusResponse(
            job_id=job_id,
            status="not_found",
            progress=ReindexProgress(),
        )
    return ReindexStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        progress=ReindexProgress(**job["progress"]),
    )
