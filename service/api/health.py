"""Health / Ready 端点。§8.6: domain 独立状态，unindexed → 503。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from service.core.config import get_config

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "rag-service", "version": "0.1.0"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    retrieval_service = request.app.state.retrieval_service
    cfg = get_config()
    domains_cfg = cfg.get("knowledge", {}).get("domains", {})
    domains_status: dict[str, Any] = {}

    for domain, domain_cfg in domains_cfg.items():
        if not domain_cfg.get("enabled", True):
            continue
        try:
            info = retrieval_service.get_index_info(domain)
            st = "ok" if info["chunk_count"] > 0 else "unindexed"
            domains_status[domain] = {"status": st, "chunk_count": info["chunk_count"], "mode": info["mode"]}
        except Exception as e:
            domains_status[domain] = {"status": "error", "message": str(e)[:200]}

    if not domains_status:
        return JSONResponse(status_code=503, content={"status": "not_ready", "service": "rag-service", "domains": {}})

    ok_n = sum(1 for d in domains_status.values() if d["status"] == "ok")
    unidx_n = sum(1 for d in domains_status.values() if d["status"] == "unindexed")
    err_n = sum(1 for d in domains_status.values() if d["status"] == "error")

    if ok_n == len(domains_status):
        overall, http_code = "ok", 200
    elif err_n > 0:
        overall, http_code = "degraded", 200
    elif unidx_n == len(domains_status):
        overall, http_code = "unindexed", 503
    else:
        overall, http_code = "degraded", 200

    return JSONResponse(status_code=http_code, content={
        "status": overall, "service": "rag-service", "domains": domains_status,
        "components": {"vector_available": retrieval_service.vector_available, "rerank_available": retrieval_service.rerank_available},
    })
