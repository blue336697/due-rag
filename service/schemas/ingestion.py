"""文档摄取 API 与统一中间模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class DocumentElement(BaseModel):
    element_id: str
    type: Literal["title", "heading", "paragraph", "list", "table", "code"]
    text: str
    page: Optional[int] = None
    heading_path: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class QualityReport(BaseModel):
    passed: bool
    requires_review: bool = False
    text_chars: int = 0
    element_count: int = 0
    warnings: List[str] = Field(default_factory=list)


class CanonicalDocument(BaseModel):
    document_id: str
    domain: str
    source_filename: str
    source_type: str
    source_hash: str
    version: int = 1
    title: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    elements: List[DocumentElement] = Field(default_factory=list)
    normalized_markdown: str = ""
    quality: QualityReport
    created_at: datetime = Field(default_factory=datetime.now)


class IngestionJobResponse(BaseModel):
    document_id: str
    job_id: str
    status: str
    status_url: str


class IngestionStatusResponse(BaseModel):
    job_id: str
    document_id: Optional[str] = None
    status: str
    stage: str = "queued"
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class DocumentPreviewResponse(BaseModel):
    document_id: str
    status: str
    normalized_markdown: str
    quality: QualityReport
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentActionResponse(BaseModel):
    document_id: str
    status: str
    reindex_job_id: Optional[str] = None
