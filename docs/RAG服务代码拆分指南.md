# RAG 服务代码拆分指南

创建日期: 2026-07-08

## 1. 目标

将当前 RAG 从 `backend/app` 提取到与 `backend/app` 平级的 `backend/rag-service`。拆分完成后:

- payment 后端只保留 `RAGClient` 和工具调用适配。
- `backend/app/rag` 不再加载 embedding、reranker、向量索引或本地 HybridRetriever。
- RAG Service 独立负责知识库加载、分块、索引、检索、重排、引用构造和知识库问答。
- `knowledge_qa_tool` 只调用 `/rag/answer`。
- `rag_search_tool` 只调用 `/rag/search`。

## 2. 本文边界

本文只回答一个问题: **本地代码服务端要改到哪一步才算拆分完成。**

本地代码服务端需要完成:

- 新建 `backend/rag-service` 代码目录。
- 将现有 RAG 代码从 `backend/app/rag` 移入 RAG Service 结构。
- 在 `backend/rag-service` 内提供 FastAPI app、API schema 和业务模块。
- 在 payment 后端新增 `backend/app/rag/client.py`。
- 改造 `knowledge_qa_tool`、`rag_search_tool`、`admin/rag.py` 只调用远程 RAG API。
- 清理 `backend/app/rag/__init__.py` 的本地执行导出。
- 保留 `RAG_REMOTE_ENABLED` 灰度开关，最终状态固定走远程 RAG。
- 补齐单元测试、工具集成测试和远程 RAG eval 调用入口。

本地代码服务端做到以下程度即可停止:

```text
backend/rag-service 能在本地启动 FastAPI
payment 后端能通过 RAGClient 调用 RAG Service
payment 后端不再直接执行本地 RAG 检索
```

以下内容不属于本文执行范围，必须去看 [RAG服务部署与接入指南.md](RAG服务部署与接入指南.md):

- 服务器上安装 Qdrant。
- 服务器上安装 Redis。
- 服务器上下载 embedding/reranker 模型。
- 服务器目录规划和挂载。
- Qdrant collection 创建、payload index、snapshot/restore。
- 生产 Docker Compose、systemd、Kubernetes 部署。
- 服务器知识库同步和线上 reindex。
- 生产性能指标、服务器健康检查和运维回滚。

一句话: **本文管代码拆分和调用关系；部署指南管服务器、模型、向量库和生产接入。**

## 3. 代码拆分完成定义

代码拆分完成时，本地仓库应满足:

| 项 | 完成标准 | 是否需要部署指南 |
| --- | --- | --- |
| `backend/rag-service` | 目录和 FastAPI app 存在 | 否 |
| `/rag/search` | API schema 和 handler 存在，可连本地/测试依赖 | 否 |
| `/rag/answer` | API schema 和 handler 存在，可连本地/测试依赖 | 否 |
| `config_gen_retriever.py` | 已从 payment 本地 RAG 中移除；是否接入生产另立任务 | 否 |
| `RAGClient` | 使用 `httpx.AsyncClient` 远程调用 | 否 |
| `knowledge_qa_tool` | 不再 import 本地 `answer_question` 执行 | 否 |
| `rag_search_tool` | 不再静默吞异常，不再直接 `retrieve()` | 否 |
| `admin/rag.py` | Form 转 JSON 或改为 JSON 后透传远程 | 否 |
| `backend/app/rag/__init__.py` | 不再导出本地 `retrieve/get_index_info` | 否 |
| Qdrant 容器 | 服务器部署项 | 是 |
| Redis 容器 | 服务器部署项 | 是 |
| 模型下载 | 服务器部署项 | 是 |
| 生产 reindex | 服务器部署项 | 是 |

因此，读者只做本地代码拆分时，读完本文即可开始改代码；准备服务器部署、下载模型、创建向量库时再读部署指南。

## 4. 需要提取的现有入口

- `backend/app/rag/retriever.py`
- `backend/app/rag/hybrid_retriever.py`
- `backend/app/rag/vector_store.py`
- `backend/app/rag/document_loader.py`
- `backend/app/rag/cross_encoder_compressor.py`
- `backend/app/rag/config_gen_retriever.py`
- `backend/app/rag/knowledge_qa.py`
- `backend/app/rag/__init__.py`
- `backend/app/feedback/inject.py`
- `backend/app/main.py`
- `backend/app/evals/run_rag_eval.py`
- `backend/app/tools/agent_tools/knowledge_qa_tool.py`
- `backend/app/tools/rag_search_tool.py`
- `backend/app/tools/agent_tools/three_agent_config_gen.py`
- `backend/app/api/admin/rag.py`
- `backend/tests/test_rag_regression.py`
- `backend/tests/test_rag_knowledge_qa.py`
- `backend/tests/test_config_gen_rag.py`

## 5. 目标目录

```text
backend/
  app/
    rag/
      client.py
      __init__.py
  rag-service/
    service/
      main.py
      api/
        health.py
        rag.py
        admin.py
      core/
        config.py
        logging.py
        security.py
        synonyms.py
      knowledge/
        loader.py
        chunker.py
        metadata.py
      retrieval/
        keyword.py
        vector.py
        fusion.py
        rerank.py
        config_gen.py  # experimental
        service.py
      models/
        embedding.py
        reranker.py
        answer_llm.py
      schemas/
        rag.py
      storage/
        qdrant.py
        bm25.py
      scripts/
        build_index.py
        download_models.py
    config/
      rag_service.yaml
    docker/
      Dockerfile
      docker-compose.yml
    requirements.txt
    README.md
```

## 6. 模块迁移映射

| 现有文件 | 新位置 | 说明 |
| --- | --- | --- |
| `document_loader.py` | `knowledge/loader.py`, `knowledge/chunker.py` | 文档加载、frontmatter、分块 |
| `hybrid_retriever.py` | `retrieval/*` | 拆成 keyword、vector、fusion、rerank、service |
| `vector_store.py` | `storage/qdrant.py` | 从 InMemoryVectorStore 改成 Qdrant |
| `cross_encoder_compressor.py` | `models/reranker.py` | CrossEncoder 加载和推理 |
| `config_gen_retriever.py` | `retrieval/config_gen.py` | 配置生成知识库检索，服务端读取 `流水配置生成 RAG增强/` |
| `knowledge_qa.py` | `retrieval/service.py`, `models/answer_llm.py` | 检索后带引用回答 |
| `retriever.py` | `retrieval/service.py` | RAG Service 内部统一入口 |
| `__init__.py` | 改为只导出 remote client 或删除旧导出 | 移除 `retrieve`、`get_index_info` 等本地执行导出 |
| `feedback/inject.py` | 保留并改造 | 删除 `_rag_search_feedback()` fallback，不再把知识库 chunk 包装成反馈记忆 |
| `main.py` | 保留并改造 | `/ready` 中 RAG 检查改调 RAG Service `/ready` |
| `evals/run_rag_eval.py` | 保留并改造 | 支持 remote eval；本地 HybridRetriever eval 移到 RAG Service 测试 |
| `knowledge_qa_tool.py` | 保留并改造 | 调用 `RemoteRAGClient.answer()` |
| `rag_search_tool.py` | 保留并改造 | 调用 `RemoteRAGClient.search()` |
| `three_agent_config_gen.py` | 保留；本次不接 RAG | 当前生产链路未调用 `config_gen_retriever.py` |
| `api/admin/rag.py` | 保留并改造 | 透传 RAG Service 调试接口 |

## 7. 范围澄清

### 7.1 配置生成 RAG

`backend/app/rag/config_gen_retriever.py` 不能遗漏。它当前指向:

```text
backend/knowledge_base/流水配置生成 RAG增强/
```

当前生产代码中，`three_agent_config_gen.py` 没有调用该模块；仓库内主要是 `backend/tests/test_config_gen_rag.py` 在引用它。因此它的边界必须按“未接入生产的独立 RAG 能力”处理，不能假设三智能体配置生成链路已经在使用它。

迁移策略:

- 本次拆分不能把它当作生产链路必迁依赖。
- 从 `backend/app/rag` 移除，避免 payment 后端继续保留本地 RAG 模块。
- 若要保留这份能力，应迁到 `backend/rag-service/service/retrieval/config_gen.py` 并标注 `experimental`。
- 不在本次接入 `three_agent_config_gen.py`，除非另开任务先把三智能体配置生成链路接线。
- 对应测试迁到 RAG Service 的 experimental 测试目录，或在确认无用后删除模块和测试。

若后续明确接入生产，RAG Service 再提供:

```text
POST /rag/config-gen/repair-strategies
POST /rag/config-gen/generation-guide
```

在接入前，不得在说明中声称当前生产链路已经依赖该 RAG。

### 7.2 知识库目录归属

知识库运行时统一归属 RAG Service。服务器侧挂载如下，但挂载和同步不在本文执行，详见部署指南:

```text
/data/payment-rag/knowledge_base/流水问答
/data/payment-rag/knowledge_base/流水配置生成 RAG增强
```

payment 后端不再直接读取 `backend/knowledge_base/*` 做 RAG 检索。仓库内仍可保留 Markdown 作为源文件，部署时同步到服务器知识库目录。

### 7.3 Qdrant 是基础设施迁移

`InMemoryVectorStore -> Qdrant` 不是简单文件移动，属于服务器侧基础设施变更。本文只要求把本地向量存储接口抽象成 `storage/qdrant.py` 和 `retrieval/vector.py`，真正的 Qdrant 部署、collection schema、索引构建、snapshot/restore 和运维验收放在 [RAG服务部署与接入指南.md](RAG服务部署与接入指南.md)。

### 7.4 其他本地 RAG 依赖调用点

除工具和 admin 接口外，还必须处理:

- `backend/app/feedback/inject.py`: `_rag_search_feedback()` 当前直接 `from backend.app.rag.retriever import retrieve`，并把知识库 chunk 包装成 `FeedbackMemory(confidence=0.6)`。这不是严格的反馈记忆，迁移时直接删除该 fallback；S4 只保留真实 feedback memory 和 episodic memory 搜索。未来如需“RAG 辅助记忆建议”，另设 feature，不复用 FeedbackMemory。
- `backend/app/main.py`: `/ready` 当前尝试 `get_vector_store().count()` 检查本地 RAG 索引。迁移后必须改为调用 RAG Service `/ready`，或将 RAG readiness 从 payment `/ready` 中拆出为远程依赖检查。
- `backend/app/evals/run_rag_eval.py`: 当前直接 import `HybridRetriever`、`answer_question`，还访问 `_keyword_only_retrieve()`、`_vector_recall()`、`_vectorstore`、`_chunks`、`mode` 等内部实现。迁移后不是“加 remote-url”即可，必须重写 eval。见“存量测试迁移”。

## 8. RAG Service 内部职责

`service/main.py`:

- 创建 FastAPI app。
- 注册 `/health`、`/ready`、`/rag/search`、`/rag/answer`、`/admin/reindex`。
- startup 预热 embedding、reranker、BM25 index、Qdrant collection 信息。

`core/config.py`:

- 读取 `config/rag_service.yaml`、`.env` 和环境变量。
- 管理服务端口、鉴权、知识库目录、Qdrant、Redis、模型和召回参数。

`knowledge/*`:

- 读取 Markdown。
- 解析 frontmatter。
- 生成父子块、邻域关系和文档问题增强数据。
- 生成 `chunk_id`、`content_hash`、`knowledge_version`。

`storage/qdrant.py`:

- 封装 Qdrant client。
- 提供 collection 校验、upsert、search、snapshot 调用接口。
- 本文只定义代码接口；服务器上的 Qdrant 实例由部署指南处理。

`storage/bm25.py`:

- 中文分词。
- 同义词扩展。
- BM25 index 构建。
- keyword recall。
- 标题命中、错误码命中 boost。

`retrieval/config_gen.py`:

- 加载 `流水配置生成 RAG增强/` 知识库。
- 支持 `retrieve_repair_strategies(error_codes, source_nodes, top_k)`。
- 支持 `retrieve_generation_guide(questions, top_k)`。
- 与普通流水问答知识库分 collection 或分 namespace 管理。
- 该模块默认标记为 experimental，直到 `three_agent_config_gen.py` 真实接入。

`core/synonyms.py`:

- 作为同义词 canonical source。
- 供 keyword recall、BM25 query expansion、embedding 文档增强标签共同使用。
- 迁移后不得在 `keyword.py`、`vector.py`、`storage/*` 中各自复制 `_SYNONYM_GROUPS`。

`retrieval/*`:

```text
query
-> keyword recall
-> vector recall
-> RRF fusion
-> CrossEncoder rerank
-> parent/neighbor context enrich
-> citation builder
```

`models/*`:

- 加载本地 embedding 模型。
- 加载本地 reranker 模型。
- 调用 LLM 生成带引用答案。
- 模型预热和 readiness 检查。
- 本文只实现加载接口和配置项；服务器模型下载和挂载由部署指南处理。

### 8.1 LangChain 依赖策略

当前本地 RAG 使用了若干 LangChain 类型:

- `MarkdownHeaderTextSplitter`
- `langchain_core.documents.Document`
- `BaseDocumentCompressor`
- `InMemoryVectorStore`
- `langchain_huggingface.HuggingFaceEmbeddings`

迁移后的目标是 **RAG Service 原生化**:

| 当前依赖 | 迁移策略 |
| --- | --- |
| `InMemoryVectorStore` | 移除，改用 Qdrant client |
| `BaseDocumentCompressor` | 移除，直接封装 sentence-transformers `CrossEncoder` |
| `langchain_huggingface.HuggingFaceEmbeddings` | 移除，直接使用 sentence-transformers `SentenceTransformer` |
| `langchain_core.documents.Document` | 移除，改用自定义 `Chunk`/Pydantic schema |
| `MarkdownHeaderTextSplitter` | 允许短期保留；最终建议替换为自研 Markdown 标题分块，减少 LangChain 依赖面 |

`backend/rag-service/requirements.txt` 原则:

- 不引入 `langchain` 大包。
- 不使用 LangChain vector store 和 compressor。
- 如果短期继续使用 `MarkdownHeaderTextSplitter`，只引入最小必要依赖，并在代码中标注为迁移债务。

### 8.2 Domain 隔离

RAG Service 至少有两个 domain:

| domain | 知识库 | 检索方式 | 依赖 embedding/Qdrant | 依赖 reranker |
| --- | --- | --- | --- | --- |
| `bank_stmt` | `流水问答` | hybrid: keyword + vector + RRF + rerank | 是 | 是 |
| `config_gen` | `流水配置生成 RAG增强` | keyword-only 或独立 BM25 | 否，除非后续显式升级 | 否 |

隔离要求:

- `bank_stmt` embedding/reranker 加载失败时，`config_gen` 的 keyword-only API 仍应可用。
- 两个 domain 使用独立 collection 或 namespace。
- 两个 domain 可以共享同义词 canonical source，但索引、manifest、readiness 独立。
- `/ready` 应分别返回每个 domain 的状态，而不是一个模型失败导致所有 domain 都显示不可用。

## 9. API 契约

### POST /rag/search

```json
{
  "query": "交易金额字段是什么意思",
  "domain": "bank_stmt",
  "collection": "bank_stmt_knowledge",
  "top_k": 5,
  "mode": "hybrid",
  "filters": {
    "category": "字段定义",
    "statement_type": "personal"
  }
}
```

返回:

```json
{
  "results": [
    {
      "chunk_id": "amount-001",
      "parent_id": "amount-parent",
      "source": "字段定义/amount.md",
      "heading": "交易金额",
      "heading_path": ["字段定义", "交易金额"],
      "content": "交易金额是...",
      "score": 0.9281,
      "score_breakdown": {
        "keyword_score": 0.71,
        "vector_score": 0.83,
        "rrf_score": 0.49,
        "rerank_score": 0.91,
        "final_score": 0.9281
      },
      "retriever": "hybrid",
      "citations": [
        {
          "index": 1,
          "source": "字段定义/amount.md",
          "heading": "交易金额",
          "heading_path": ["字段定义", "交易金额"],
          "chunk_id": "amount-001",
          "parent_id": "amount-parent",
          "content_hash": "sha256:..."
        }
      ]
    }
  ],
  "retriever": {
    "mode": "hybrid",
    "keyword_available": true,
    "vector_available": true,
    "rerank_available": true
  },
  "latency_ms": 86
}
```

`/rag/search` 也返回 citation metadata，便于调试、admin 检索和 Agent L4 证据归一化。payment 的 `rag_search_tool.contextualize_result()` 默认仍只把稳定的 `source/heading/content/score` 写入 `state["rag_results"]`；若后续需要在 L4 展示引用，可从 `citations` 显式映射，不能把远程原始 JSON 整包透传。

### POST /rag/answer

```json
{
  "question": "发生额和交易金额一样吗",
  "domain": "bank_stmt",
  "collection": "bank_stmt_knowledge",
  "top_k": 5,
  "with_citations": true,
  "return_retrieved": false
}
```

返回:

```json
{
  "answer": "发生额通常对应交易金额...[1]",
  "citations": [
    {
      "index": 1,
      "source": "字段定义/amount.md",
      "heading": "交易金额",
      "heading_path": ["字段定义", "交易金额"],
      "chunk_id": "amount-001",
      "parent_id": "amount-parent",
      "content_hash": "sha256:..."
    }
  ],
  "retrieved": [],
  "retriever": {
    "mode": "hybrid"
  },
  "latency_ms": 1400
}
```

### POST /admin/reindex

提交异步重建任务:

```json
{
  "collection": "bank_stmt_knowledge",
  "domain": "bank_stmt",
  "force": true
}
```

返回:

```json
{
  "job_id": "rag-reindex-20260708-001",
  "status": "queued",
  "status_url": "/admin/reindex/rag-reindex-20260708-001"
}
```

查询任务状态:

```text
GET /admin/reindex/{job_id}
```

```json
{
  "job_id": "rag-reindex-20260708-001",
  "status": "running",
  "progress": {
    "stage": "embedding",
    "processed_chunks": 42,
    "total_chunks": 180
  }
}
```

### POST /rag/eval/search

远程 eval 需要分模式测量 `keyword_recall_3`、`vector_recall_3`、`hybrid_recall_3`。普通 `/rag/search` 只返回最终融合重排结果，不能满足现有 eval。RAG Service 必须提供仅限内部/测试环境使用的 eval API:

```json
{
  "query": "交易金额字段是什么意思",
  "domain": "bank_stmt",
  "top_k": 5,
  "modes": ["keyword", "vector", "hybrid"],
  "include_raw": true
}
```

返回:

```json
{
  "keyword": [{"source": "字段定义/amount.md", "heading": "交易金额", "content": "..."}],
  "vector": [{"source": "字段定义/amount.md", "heading": "交易金额", "content": "..."}],
  "hybrid": [{"source": "字段定义/amount.md", "heading": "交易金额", "content": "..."}],
  "debug": {
    "vector_available": true,
    "chunk_count": 180,
    "mode": "hybrid"
  }
}
```

该接口替代 `run_rag_eval.py` 对私有方法的访问。生产公网不得暴露该接口。

下面两个 `config-gen` 接口是前瞻规格，本次代码拆分不要求实现，也不接入生产链路；只有在后续明确要把配置生成知识库接入服务时，才按该契约落地。

### POST /rag/config-gen/repair-strategies

```json
{
  "error_codes": ["CONFIG_CORE_FIELD_MISSING"],
  "source_nodes": ["field_mapping"],
  "top_k": 5
}
```

### POST /rag/config-gen/generation-guide

```json
{
  "questions": ["如何判断 merge_mode", "微信支付的表格特征"],
  "top_k": 5
}
```

## 10. payment 后端接入改造

新增:

```text
backend/app/rag/client.py
```

职责:

- 读取 `RAG_SERVICE_URL`。
- 注入 `Authorization` 或 `X-API-Key`。
- 设置超时。
- 统一错误处理。
- 远程服务不可用时抛出明确的 `RAGServiceUnavailable`。
- 使用 `httpx.AsyncClient`，不得在 async tool 内使用 `requests`。
- 复用连接池，设置 connect/read/write/pool timeout。

建议按接口类型区分 timeout:

```python
httpx.Limits(max_connections=50, max_keepalive_connections=20)

SEARCH_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
ANSWER_TIMEOUT = httpx.Timeout(connect=3.0, read=60.0, write=5.0, pool=3.0)
ADMIN_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)
```

说明:

- `/rag/search` 不经过 LLM，读超时应较短。
- `/rag/answer` 会等待 LLM 生成，读超时不能沿用 search 的 5 秒；默认 60 秒，与当前 `knowledge_qa_tool.timeout = 60` 对齐。
- `/admin/reindex` 只提交异步 job，submit/poll 都应快速返回；长耗时发生在 RAG Service 后台任务中。

工具改造:

```text
KnowledgeQATool.call()
  -> RemoteRAGClient.answer()
  -> RAG Service /rag/answer

RAGSearchTool.call()
  -> RemoteRAGClient.search()
  -> RAG Service /rag/search
```

`RAGSearchTool` 当前 input 只有 `query + domain`。迁移后 schema 需要扩展:

```python
class RAGSearchInput(BaseModel):
    query: str
    domain: str = "bank_stmt"
    collection: str | None = None
    top_k: int = 5
    mode: Literal["hybrid", "keyword", "vector"] = "hybrid"
    filters: dict[str, Any] = Field(default_factory=dict)
```

映射规则:

| Tool 字段 | RAG Service 字段 | 说明 |
| --- | --- | --- |
| `query` | `query` | 原样传入 |
| `domain` | `domain` | 业务域 |
| `collection` | `collection` | 未传时按 domain 映射 |
| `top_k` | `top_k` | 默认 5 |
| `mode` | `mode` | 默认 hybrid |
| `filters` | `filters` | 类目、流水类型、实体过滤 |

默认映射:

```text
bank_stmt -> bank_stmt_knowledge
config_gen -> parser_config_knowledge
```

异常处理要求:

- `rag_search_tool.py` 不能再 `except Exception: return []`。
- RAG 服务不可用时抛出明确异常，不能返回空结果掩盖故障。
- 空检索结果只表示正常检索无命中，不能用于掩盖 5xx、超时、鉴权失败或 JSON 解析失败。
- `knowledge_qa_tool.contextualize_result` 只保留 `answer` 字段，不再兼容 `response` 死代码。

异常分层:

| 异常 | 场景 | 调用方处理 |
| --- | --- | --- |
| `RAGServiceUnavailable` | 连接拒绝、DNS 失败、服务未启动 | 工具失败，提示知识服务不可用 |
| `RAGServiceTimeout` | connect/read/write/pool timeout | 可按只读工具策略重试一次；仍失败则工具失败 |
| `RAGBadRequest` | 4xx 参数错误、schema 错误 | 不重试，修正调用参数 |
| `RAGAuthError` | 401/403 | 不重试，报告配置/权限错误 |
| `RAGRemoteError` | 5xx | 可短重试；仍失败则工具失败 |
| `RAGResponseError` | JSON 解析失败、响应字段缺失 | 不重试或短重试，记录响应摘要 |

`RAGServiceUnavailable` 可以作为父类，但 client 内部必须保留细分异常类型，方便日志、监控和调用方处理。

### 10.1 rag_results 格式兼容

远程 `/rag/search` 返回结构比当前本地 retrieve 多 `chunk_id`、`parent_id`、`heading_path`、`score_breakdown`、`citations`、`retriever`、`latency_ms` 等字段。进入 AgentLoop 前必须由 tool 做一次上下文归一化，保证 `state["rag_results"]` 中的 `content` 形状稳定。

推荐 `rag_search_tool.contextualize_result()` 输出:

```json
{
  "results": [
    {
      "source": "字段定义/amount.md",
      "heading": "交易金额",
      "content": "交易金额是...",
      "score": 0.9281
    }
  ],
  "count": 1,
  "mode": "hybrid"
}
```

`citations` 是远程 API 证据元数据，默认不透传进 `state["rag_results"]`。只有 ContextAssembler 明确支持 citation 展示字段后，tool 才能把它转换成稳定的 L4 引用结构。

推荐 `knowledge_qa_tool.contextualize_result()` 输出:

```json
{
  "answer": "...",
  "citations": [
    {
      "source": "字段定义/amount.md",
      "heading": "交易金额",
      "heading_path": ["字段定义", "交易金额"],
      "chunk_id": "amount-001",
      "parent_id": "amount-parent",
      "content_hash": "sha256:..."
    }
  ]
}
```

ContextAssembler 只消费归一化后的 `content` 和稳定 citation 字段，不直接依赖远程 API 原始结构。

管理接口改造:

```text
POST /admin/rag/search
  -> RemoteRAGClient.search()
  -> RAG Service /rag/search
```

当前 `admin/rag.py` 使用 `Form(...)` 参数，RAG Service 使用 JSON body。迁移时需要二选一:

- 管理端接口同步改为 JSON body。
- 或在 payment 管理接口中保留 Form，对内转换为 JSON 后调用 RAG Service。

推荐改为 JSON body，和 RAG Service API 保持一致。

`backend/app/rag/__init__.py` 清理:

- 删除或停止导出本地 `retrieve`。
- 删除或停止导出本地 `get_index_info`。
- 仅导出 `RemoteRAGClient`、`RAGServiceUnavailable` 等远程调用对象。

`feedback/inject.py` 改造:

```text
search_feedback_memory()
  -> 只查真实 FeedbackMemory
  -> 没命中则返回 []
```

删除 `_rag_search_feedback()`。原因: RAG chunk 不是用户反馈记忆，不能再包装成 `FeedbackMemory(confidence=0.6)` 注入 S4 记忆层。

`main.py /ready` 改造:

```text
payment /ready
  -> db check
  -> redis check
  -> rag service /ready
  -> llm check
```

RAG Service `/ready` 失败时，payment `/ready` 应显示 `rag_service: error/degraded`，不再 import `backend.app.rag.vector_store`。

推荐处理:

```text
RAG Service /ready ok      -> payment /ready checks.rag_service = ok
RAG Service /ready error   -> payment /ready status = degraded, checks.rag_service = error
RAG Service connection err -> payment /ready status = degraded, checks.rag_service = unavailable
```

## 11. S1 上下文兼容

两个工具继续返回:

```text
context_lane() == "rag"
```

AgentLoop 继续写入:

```text
state["rag_results"]
```

ContextAssembler 继续放入 L4:

```text
[RAG_RESULT source=knowledge_base]
```

## 12. 过渡开关和最终状态

生产迁移需要显式 feature flag:

```bash
RAG_REMOTE_ENABLED=true
RAG_SERVICE_URL=http://rag-server:8020
RAG_SERVICE_API_KEY=change-me
```

过渡期允许通过 `RAG_REMOTE_ENABLED=false` 回到旧路径，以便灰度和回滚。最终状态必须为:

```text
RAG_REMOTE_ENABLED=true
payment 后端不加载本地 RAG 模型
payment 后端不构建本地向量索引
```

LLM 配置边界:

- payment 后端继续使用 `backend/config/llm.yaml` 和对应 `.env` secret。
- RAG Service 使用自己的 `backend/rag-service/config/rag_service.yaml` 和部署环境变量。
- 两者可以指向同一个 LLM 网关和模型，但配置文件不共享、不互相 import。
- RAG Service 需要的 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 由服务器部署注入。
- 如果 `/rag/answer` 调用同一 LLM 端点，也只是“配置值相同”，不是代码层共享 payment 的 llm loader。

`rag_service.yaml` 需要有独立 `llm` 段:

```yaml
llm:
  base_url: ${LLM_BASE_URL}
  api_key_env: LLM_API_KEY
  model: ${LLM_MODEL}
  timeout_seconds: 60
```

## 13. 本地代码构建顺序

1. 新建 `backend/rag-service` 目录和 Python 包结构。
2. 提取 `backend/app/rag` 内的文档加载、检索、向量、重排、问答逻辑。
3. 从 payment 本地 RAG 中移除 `config_gen_retriever.py`；若保留则迁到 RAG Service experimental 模块，但不接生产链路。
4. 建 FastAPI app、API schema、配置加载和错误模型。
5. 定义 Qdrant、Redis、embedding、reranker、LLM 的代码适配接口。
6. 实现 `/rag/search`、`/rag/answer`、`/admin/reindex` 的 handler。
7. 新增 payment 后端 `RAGClient`。
8. 改造 `knowledge_qa_tool` 和 `rag_search_tool`。
9. 改造管理端 RAG 调试接口。
10. 清理 `backend/app/rag/__init__.py` 和本地执行导出。
11. 增加本地单元测试、client mock 测试、工具集成测试和远程 RAG eval 调用入口。

做到第 11 步，本地代码拆分工作结束。随后如果要启动真实 Qdrant、下载模型、构建生产索引，转到 [RAG服务部署与接入指南.md](RAG服务部署与接入指南.md)。

## 14. 本地代码验收标准

- `backend/rag-service` 可独立启动。
- `backend/rag-service` API schema 和 handler 完整。
- `config_gen_retriever.py` 不再留在 payment 本地 RAG；若保留，已标注 experimental 且不接生产链路。
- payment 后端不再加载 embedding/reranker。
- payment 后端不再构建本地向量索引。
- `knowledge_qa_tool` 只调用远程 `/rag/answer`。
- `rag_search_tool` 只调用远程 `/rag/search`。
- `rag_search_tool` 不再静默吞异常。
- `admin/rag.py` 已处理 Form 到 JSON 的契约差异或改为 JSON。
- `backend/app/rag/__init__.py` 不再导出本地执行入口。
- AgentLoop 的 `rag_results` 和 ContextAssembler L4 行为保持不变。
- 本地 mock 测试能证明 payment 后端会调用远程 RAG API。
- 真实服务器上的远程 RAG eval 指标验收在部署指南中完成。

## 15. 存量测试和 eval 迁移

现有测试不能直接删除，按以下方式迁移:

| 当前测试/脚本 | 处理方式 |
| --- | --- |
| `backend/tests/test_rag_knowledge_qa.py` | 拆成 RAG Service 内部单元测试；payment 侧保留 `RAGClient` mock 测试 |
| `backend/tests/test_rag_regression.py` | 改为远程 `/rag/answer` 回归测试，支持 `RAG_SERVICE_URL` |
| `backend/tests/test_config_gen_rag.py` | 本次不进入常规 CI；若保留 `config_gen` experimental 模块，先迁到 RAG Service experimental 测试目录；只有后续实现 `/rag/config-gen/*` 前瞻接口时，才补 HTTP 集成测试 |
| `backend/app/evals/run_rag_eval.py` | 重写为远程 eval；通过 `/rag/eval/search` 获取 keyword/vector/hybrid 三路结果 |

`run_rag_eval.py` 不能继续访问以下私有实现:

```text
retriever._keyword_only_retrieve(...)
retriever._vector_recall(...)
retriever._vectorstore
retriever._chunks
retriever.mode
```

远程 eval 有两种允许方案:

1. **保留分模式指标**: RAG Service 实现 `/rag/eval/search`，返回 keyword/vector/hybrid 三路结果。
2. **端到端指标**: 放弃 `keyword_recall_3`、`vector_recall_3`，只保留 `hybrid_recall_3`、`source_coverage`、`answer_rate`、`refusal_accuracy`。

当前为了延续 baseline，选择方案 1。

eval 数据归属:

- canonical 数据迁到 `backend/rag-service/evals/datasets/rag_cases.jsonl`。
- canonical baseline 迁到 `backend/rag-service/evals/baselines/rag_baseline.json`。
- payment 后端不维护第二份 RAG baseline，只保留远程调用 smoke/e2e 测试。
- 如果 payment 侧 CI 需要跑 RAG 质量门禁，应通过 RAG Service URL 调用 canonical eval。

payment 后端测试重点:

- `knowledge_qa_tool` 调用了 `RemoteRAGClient.answer()`。
- `rag_search_tool` 调用了 `RemoteRAGClient.search()`。
- `rag_search_tool` 对 5xx/timeout 不返回空结果。
- `feedback/inject.py` 的 RAG fallback 不再 import 本地 retriever。
- `/ready` 不再 import 本地 vector store。
