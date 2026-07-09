"""RAG 检索和问答 API 端点。

POST /rag/search  — 知识库检索
POST /rag/answer  — 知识库问答
POST /rag/eval/search — eval 多模式检索（内部/测试环境）
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from service.core.config import get_config

from service.schemas.rag import (
    AnswerRequest,
    AnswerResponse,
    Citation,
    EvalSearchRequest,
    EvalSearchResponse,
    RetrieverMeta,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])


def _get_retrieval_service(request: Request) -> Any:
    return request.app.state.retrieval_service


def _validate_domain(domain: str) -> None:
    cfg = get_config()
    domains = cfg.get("knowledge", {}).get("domains", {})
    if domain not in domains:
        raise HTTPException(
            status_code=404,
            detail={"code": "domain_not_found", "domain": domain},
        )
    if not domains[domain].get("enabled", True):
        raise HTTPException(
            status_code=400,
            detail={"code": "domain_disabled", "domain": domain},
        )


@router.post("/search", response_model=SearchResponse)
def search(request: Request, body: SearchRequest) -> SearchResponse:
    _validate_domain(body.domain)
    svc = _get_retrieval_service(request)
    result = svc.search(
        query=body.query,
        domain=body.domain,
        top_k=body.top_k,
        mode=body.mode,
        filters=body.filters or None,
    )
    retriever_meta = result["retriever"]
    return SearchResponse(
        results=[
            SearchResult(
                chunk_id=r.get("chunk_id", ""),
                parent_id=r.get("parent_id"),
                source=r.get("source", ""),
                heading=r.get("heading") or (r.get("heading_path", [])[-1] if r.get("heading_path") else ""),
                heading_path=r.get("heading_path", []),
                content=r.get("content", ""),
                score=r.get("score", r.get("final_score", 0.0)),
                score_breakdown=r.get("score_breakdown", {}),
                retriever=r.get("retriever", retriever_meta.get("mode", "hybrid")),
                citations=[Citation(**c) if isinstance(c, dict) else c for c in r.get("citations", [])[:5]],
                metadata=r.get("metadata", {}),
            )
            for r in result["results"]
        ],
        retriever=RetrieverMeta(**retriever_meta),
        latency_ms=result["latency_ms"],
    )


@router.post("/answer", response_model=AnswerResponse)
def answer(request: Request, body: AnswerRequest) -> AnswerResponse:
    _validate_domain(body.domain)
    svc = _get_retrieval_service(request)
    result = svc.answer(
        question=body.question,
        domain=body.domain,
        top_k=body.top_k,
        with_citations=body.with_citations,
        return_retrieved=body.return_retrieved,
    )
    return AnswerResponse(
        answer=result["answer"],
        citations=[
            Citation(**c) if isinstance(c, dict) else c
            for c in result.get("citations", [])
        ],
        retrieved=[
            SearchResult(
                chunk_id=r.get("chunk_id", ""),
                parent_id=r.get("parent_id"),
                source=r.get("source", ""),
                heading=r.get("heading") or (r.get("heading_path", [])[-1] if r.get("heading_path") else ""),
                heading_path=r.get("heading_path", []),
                content=r.get("content", ""),
                score=r.get("score", r.get("final_score", 0.0)),
                score_breakdown=r.get("score_breakdown", {}),
                retriever=r.get("retriever", result.get("retriever", {}).get("mode", "hybrid")),
            )
            for r in result.get("retrieved", [])
        ],
        retriever=RetrieverMeta(**result["retriever"]),
        latency_ms=result["latency_ms"],
    )


@router.post("/eval/search", response_model=EvalSearchResponse)
def eval_search(request: Request, body: EvalSearchRequest) -> EvalSearchResponse:
    """仅限内部/测试环境使用的 eval API。生产公网不得暴露。"""
    cfg = get_config()
    if not cfg.get("eval", {}).get("enable_debug_api", False):
        raise HTTPException(
            status_code=404,
            detail={"code": "debug_api_disabled"},
        )
    _validate_domain(body.domain)
    svc = _get_retrieval_service(request)
    result = svc.search_multi_mode(
        query=body.query,
        domain=body.domain,
        top_k=body.top_k,
        modes=body.modes,
    )
    return EvalSearchResponse(
        keyword=[
            SearchResult(
                chunk_id=r.get("chunk_id", ""),
                source=r.get("source", ""),
                heading=r.get("heading") or (r.get("heading_path", [])[-1] if r.get("heading_path") else ""),
                heading_path=r.get("heading_path", []),
                content=r.get("content", ""),
                score=r.get("score", r.get("final_score", 0.0)),
                score_breakdown=r.get("score_breakdown", {}),
                retriever="keyword",
            )
            for r in result.get("keyword", [])
        ],
        vector=[
            SearchResult(
                chunk_id=r.get("chunk_id", ""),
                source=r.get("source", ""),
                heading=r.get("heading") or (r.get("heading_path", [])[-1] if r.get("heading_path") else ""),
                heading_path=r.get("heading_path", []),
                content=r.get("content", ""),
                score=r.get("score", r.get("final_score", 0.0)),
                score_breakdown=r.get("score_breakdown", {}),
                retriever="vector",
            )
            for r in result.get("vector", [])
        ],
        hybrid=[
            SearchResult(
                chunk_id=r.get("chunk_id", ""),
                source=r.get("source", ""),
                heading=r.get("heading") or (r.get("heading_path", [])[-1] if r.get("heading_path") else ""),
                heading_path=r.get("heading_path", []),
                content=r.get("content", ""),
                score=r.get("score", r.get("final_score", 0.0)),
                score_breakdown=r.get("score_breakdown", {}),
                retriever="hybrid",
            )
            for r in result.get("hybrid", [])
        ],
        debug=result.get("debug", {}),
    )
