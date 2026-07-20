"""文档上传、解析预览、发布和删除接口。"""
from __future__ import annotations

import json
from typing import Optional
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from service.ingestion.jobs import get_ingestion_job_manager
from service.ingestion.service import IngestionError
from service.reindex.jobs import get_reindex_job_manager
from service.schemas.ingestion import DocumentActionResponse, DocumentPreviewResponse, IngestionJobResponse, IngestionStatusResponse
from service.schemas.rag import ReindexRequest

router = APIRouter(prefix="/admin", tags=["documents"])


@router.post("/documents", response_model=IngestionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(file: UploadFile = File(...), domain: str = Form("bank_stmt"), metadata: str = Form("{}"), parser_profile: str = Form("auto")) -> IngestionJobResponse:
    manager = get_ingestion_job_manager()
    try:
        if parser_profile != "auto": raise ValueError("only parser_profile=auto is currently supported")
        parsed_metadata = json.loads(metadata)
        if not isinstance(parsed_metadata, dict): raise ValueError("metadata must be an object")
        limit = manager.service.options["max_upload_bytes"]
        content = await file.read(limit + 1)
        record = manager.service.accept_upload(file.filename or "upload", content, domain, parsed_metadata)
        job = manager.submit(record["document_id"])
        return IngestionJobResponse(document_id=record["document_id"], job_id=job["job_id"], status="queued", status_url=f"/admin/ingestions/{job['job_id']}")
    except (ValueError, json.JSONDecodeError, IngestionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ingestions/{job_id}", response_model=IngestionStatusResponse)
def ingestion_status(job_id: str) -> IngestionStatusResponse:
    job = get_ingestion_job_manager().get(job_id)
    if not job: raise HTTPException(status_code=404, detail="ingestion job not found")
    return IngestionStatusResponse(**job)


@router.get("/documents/{document_id}/preview", response_model=DocumentPreviewResponse)
def document_preview(document_id: str) -> DocumentPreviewResponse:
    service = get_ingestion_job_manager().service
    record = service.registry.get_document(document_id); data = service.registry.load_canonical(document_id)
    if not record or not data: raise HTTPException(status_code=404, detail="document preview not found")
    return DocumentPreviewResponse(document_id=document_id, status=record["status"], normalized_markdown=data["normalized_markdown"], quality=data["quality"], metadata=data.get("metadata", {}))


@router.post("/documents/{document_id}/publish", response_model=DocumentActionResponse, status_code=status.HTTP_202_ACCEPTED)
def publish_document(request: Request, document_id: str, force_quality: bool = False, reindex: bool = True) -> DocumentActionResponse:
    service = get_ingestion_job_manager().service
    try:
        service.publish(document_id, force=force_quality)
        record = service.registry.get_document(document_id) or {}
        reindex_job_id: Optional[str] = None
        if reindex:
            job = get_reindex_job_manager().submit(request.app.state.retrieval_service, ReindexRequest(domain=record["domain"], force=True))
            reindex_job_id = job["job_id"]
        return DocumentActionResponse(document_id=document_id, status="published", reindex_job_id=reindex_job_id)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/documents/{document_id}", response_model=DocumentActionResponse, status_code=status.HTTP_202_ACCEPTED)
def delete_document(request: Request, document_id: str, reindex: bool = True) -> DocumentActionResponse:
    service = get_ingestion_job_manager().service
    try:
        record = service.delete(document_id); reindex_job_id: Optional[str] = None
        if reindex:
            job = get_reindex_job_manager().submit(request.app.state.retrieval_service, ReindexRequest(domain=record["domain"], force=True))
            reindex_job_id = job["job_id"]
        return DocumentActionResponse(document_id=document_id, status="deleted", reindex_job_id=reindex_job_id)
    except IngestionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
