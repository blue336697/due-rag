"""RAG Service API schemas — Pydantic models for request/response and internal data.

数据模型按 RAG高级检索能力开发指南 §6 定义:
  §6.1 Chunk            — 知识块核心模型
  §6.2 SearchResult     — 检索结果（含 score_breakdown + citations）
  §6.3 Citation         — 证据引用
  §6.4 IndexManifest    — domain 索引元信息
  §6.5 ActiveIndexSnapshot — 运行期不可变快照
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# §6.1 Chunk — 知识块核心模型
# ═══════════════════════════════════════════════════════════════

class Chunk(BaseModel):
    """知识块 — 索引和检索的基本单位。

    chunk_id 生成规范: sha256(source + JSON(heading_path) + ordinal + content_hash)，
    用 "||" 分隔。禁止使用 rel_path#i 旧格式。
    """
    chunk_id: str = Field(description="chunk 稳定唯一标识，基于 source+heading_path+ordinal+content_hash 的 SHA256")
    parent_id: Optional[str] = Field(default=None, description="父块 chunk_id，供 Small-to-Big 使用")
    source: str = Field(description="来源文件相对路径，如 字段定义/amount.md")
    heading_path: list[str] = Field(default_factory=list, description="Markdown 标题层级路径，如 ['字段定义', '交易金额']")
    title: Optional[str] = Field(default=None, description="展示标题：优先 frontmatter.title → heading_path[-1] → 文件名stem")
    content: str = Field(description="chunk 原始正文，不含 frontmatter/标题前缀/embedding增强文本")
    embedding_text: Optional[str] = Field(default=None, description="仅供 embedding/reindex 使用的派生文本，禁止进入 API response/LLM context/Qdrant payload")
    content_hash: str = Field(description="规范化正文的 SHA256: sha256(normalized_content)")
    chunk_type: Literal["parent", "child"] = Field(default="child", description="块类型")
    ordinal: int = Field(default=0, description="同一 parent_id 下的 child 顺序(1开始)；parent 自身为 0")
    prev_chunk_id: Optional[str] = Field(default=None, description="前一个邻居 chunk_id（同 source + 同 parent）")
    next_chunk_id: Optional[str] = Field(default=None, description="后一个邻居 chunk_id（同 source + 同 parent）")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据（category/statement_type/entities 等）")


# ═══════════════════════════════════════════════════════════════
# §6.3 Citation — 证据引用
# ═══════════════════════════════════════════════════════════════

class Citation(BaseModel):
    """证据引用 — RAG Service 的核心输出。

    heading 只作为展示字段保留，内部定位以 heading_path 为准。
    """
    index: int = Field(description="引用编号，从 1 开始按最终 rerank 顺序生成")
    source: str = Field(description="来源文件相对路径")
    heading: Optional[str] = Field(default=None, description="展示用标题，取 heading_path[-1]")
    heading_path: list[str] = Field(default_factory=list, description="Markdown 标题层级路径")
    chunk_id: str = Field(description="证据来源 chunk_id")
    parent_id: Optional[str] = Field(default=None, description="证据来源 parent chunk_id")
    content_hash: str = Field(description="证据 chunk 的内容 hash，用于追溯版本")
    quote: Optional[str] = Field(default=None, description="短摘录，用于定位证据，不返回整段原文")


# ═══════════════════════════════════════════════════════════════
# §6.2 SearchResult — 检索结果
# ═══════════════════════════════════════════════════════════════

class SearchResult(BaseModel):
    """单条检索结果 — 包含完整 score_breakdown 和 citations。"""
    chunk_id: str = Field(description="chunk 唯一标识")
    parent_id: Optional[str] = Field(default=None, description="父块 chunk_id")
    source: str = Field(description="来源文件路径")
    heading: Optional[str] = Field(default=None, description="展示标题，取 heading_path[-1]")
    heading_path: list[str] = Field(default_factory=list, description="Markdown 标题层级路径")
    content: str = Field(description="chunk 原始正文")
    score: float = Field(description="最终相关性分数")
    score_breakdown: dict[str, Optional[float]] = Field(
        default_factory=lambda: {
            "keyword_score": None,
            "vector_score": None,
            "rrf_score": None,
            "rerank_score": None,
            "final_score": None,
        },
        description="分阶段评分明细；未参与阶段填 null",
    )
    retriever: Literal["keyword", "vector", "hybrid"] = Field(default="hybrid", description="检索方式")
    citations: list[Citation] = Field(default_factory=list, description="证据引用列表")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")


# ═══════════════════════════════════════════════════════════════
# §6.4 IndexManifest — domain 索引元信息
# ═══════════════════════════════════════════════════════════════

class IndexManifest(BaseModel):
    """domain 顶层索引 manifest。

    写入路径: indexes/manifests/<domain>.json
    version 是单调递增的 domain 索引 generation，每次 reindex 成功后自增 1。
    """
    version: int = Field(description="单调递增的 domain 索引 generation")
    domain: str = Field(description="业务域")
    collection: Optional[str] = Field(default=None, description="Qdrant collection 名称")
    bm25_namespace: str = Field(description="BM25 namespace")
    knowledge_version: str = Field(description="知识库内容 fingerprint 或外部发布版本")
    chunk_version: str = Field(description="chunking 算法版本，变更后需重建索引")
    embedding_model_id: Optional[str] = Field(default=None, description="embedding 模型 ID")
    embedding_revision: Optional[str] = Field(default=None, description="embedding 模型 revision")
    vector_size: Optional[int] = Field(default=None, description="向量维度")
    distance: Optional[str] = Field(default=None, description="向量距离度量")
    chunk_count: int = Field(default=0, description="总 chunk 数")
    parent_count: int = Field(default=0, description="parent chunk 数")
    child_count: int = Field(default=0, description="child chunk 数")
    content_hash: str = Field(description="索引内容完整性 hash: sha256(排序后的 chunk_id+content_hash 行)")
    created_at: datetime = Field(default_factory=datetime.now, description="manifest 创建时间")


# ═══════════════════════════════════════════════════════════════
# §6.5 ActiveIndexSnapshot — 运行期不可变快照
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ActiveIndexSnapshot:
    """RetrievalService 运行期不可变快照 — 不写入 API response。

    每次 /rag/search 请求开始时只读取一次 snapshot 引用。
    发布事务只能用完整的新 snapshot 替换旧引用。
    """
    domain: str
    version: int
    pipeline_profile: Literal["hybrid_advanced", "keyword_simple"]
    retrieval_mode: Literal["hybrid", "keyword", "vector"]
    qdrant_alias: Optional[str]
    qdrant_collection: Optional[str]
    bm25_namespace: str
    bm25_current_dir: str
    bm25_manifest: dict[str, Any]
    bm25_reader: Any
    chunks_by_id: Mapping[str, Chunk]
    manifest: IndexManifest
    ready_status: str
    loaded_at: datetime


# ═══════════════════════════════════════════════════════════════
# Request / Response models
# ═══════════════════════════════════════════════════════════════

class SearchRequest(BaseModel):
    query: str = Field(description="检索查询")
    domain: str = Field(default="bank_stmt", description="业务域")
    collection: Optional[str] = Field(default=None, description="collection 名称，未传时按 domain 映射")
    top_k: int = Field(default=5, ge=1, le=50, description="返回结果数")
    mode: Literal["hybrid", "keyword", "vector"] = Field(default="hybrid", description="检索模式")
    filters: Dict[str, Any] = Field(default_factory=dict, description="元数据过滤条件（同时作用于 keyword 和 vector recall）")


class RetrieverMeta(BaseModel):
    mode: str = Field(description="检索模式")
    keyword_available: bool = Field(default=False)
    vector_available: bool = Field(default=False)
    rerank_available: bool = Field(default=False)


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)
    retriever: RetrieverMeta = Field(default_factory=lambda: RetrieverMeta(mode="none"))
    latency_ms: float = Field(default=0.0)


class AnswerRequest(BaseModel):
    question: str = Field(description="自然语言问题")
    domain: str = Field(default="bank_stmt", description="业务域")
    collection: Optional[str] = Field(default=None, description="collection 名称")
    top_k: int = Field(default=5, ge=1, le=20, description="检索 chunk 数")
    with_citations: bool = Field(default=True, description="是否返回引用")
    return_retrieved: bool = Field(default=False, description="是否返回检索明细")


class AnswerResponse(BaseModel):
    answer: str = Field(description="LLM 生成的带引用答案")
    answer_mode: str = Field(default="llm", description="成功响应固定为 llm；LLM 失败时接口返回 HTTP 502")
    degraded: bool = Field(default=False, description="兼容字段；成功响应固定为 false")
    error_reason: str = Field(default="", description="兼容字段；成功响应固定为空，失败详情由 HTTP 错误契约表达")
    citations: list[Citation] = Field(default_factory=list)
    retrieved: list[SearchResult] = Field(default_factory=list)
    retriever: RetrieverMeta = Field(default_factory=lambda: RetrieverMeta(mode="none"))
    latency_ms: float = Field(default=0.0)


class ReindexRequest(BaseModel):
    collection: Optional[str] = Field(default=None, description="目标 collection 名称；传入时必须与 domain 配置一致")
    domain: str = Field(default="bank_stmt", description="业务域")
    force: bool = Field(default=False, description="是否强制重建")


class ReindexProgress(BaseModel):
    stage: str = Field(default="queued", description="当前阶段")
    processed_chunks: int = Field(default=0)
    total_chunks: int = Field(default=0)


class ReindexResponse(BaseModel):
    job_id: str = Field(description="异步任务 ID")
    status: str = Field(default="queued", description="任务状态")
    status_url: str = Field(default="", description="状态查询 URL")


class ReindexStatusResponse(BaseModel):
    job_id: str = Field(description="任务 ID")
    status: str = Field(default="unknown", description="任务状态")
    progress: ReindexProgress = Field(default_factory=ReindexProgress)
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = Field(default=None)


class EvalSearchRequest(BaseModel):
    query: str = Field(description="检索查询")
    domain: str = Field(default="bank_stmt", description="业务域")
    top_k: int = Field(default=5, ge=1, le=50)
    modes: list[Literal["keyword", "vector", "hybrid"]] = Field(
        default_factory=lambda: ["keyword", "vector", "hybrid"],
        description="要测量的检索模式",
    )
    include_raw: bool = Field(default=False, description="是否返回调试信息")


class EvalSearchResponse(BaseModel):
    keyword: list[SearchResult] = Field(default_factory=list)
    vector: list[SearchResult] = Field(default_factory=list)
    hybrid: list[SearchResult] = Field(default_factory=list)
    debug: Dict[str, Any] = Field(default_factory=dict)
