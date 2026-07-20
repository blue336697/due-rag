"""管理端点 — reindex 异步任务。

POST /admin/reindex        — 提交异步重建任务
GET  /admin/reindex/{job_id} — 查询任务状态
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Request, status

from service.schemas.rag import (
    ReindexProgress,
    ReindexRequest,
    ReindexResponse,
    ReindexStatusResponse,
)

from service.reindex.jobs import get_reindex_job_manager

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

@router.post("/reindex", response_model=ReindexResponse, status_code=status.HTTP_202_ACCEPTED)
def reindex(request: Request, body: ReindexRequest) -> ReindexResponse:
    svc = request.app.state.retrieval_service
    job = get_reindex_job_manager().submit(svc, body)

    return ReindexResponse(
        job_id=job["job_id"],
        status="queued",
        status_url=f"/admin/reindex/{job['job_id']}",
    )


@router.get("/reindex/{job_id}", response_model=ReindexStatusResponse)
def reindex_status(job_id: str) -> ReindexStatusResponse:
    job = get_reindex_job_manager().get(job_id)
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
        result=job.get("result", {}),
        error=job.get("error"),
    )
