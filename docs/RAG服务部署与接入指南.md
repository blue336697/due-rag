# RAG 服务部署与接入指南

创建日期: 2026-07-08

## 1. 目标

本文说明 `backend/rag-service` 如何在服务器上部署，包括依赖安装、模型下载、Qdrant/Redis 部署、服务打包启动、知识库同步、索引构建和 payment 后端对接。

代码拆分方案见 [RAG服务代码拆分指南.md](RAG服务代码拆分指南.md)。

## 2. 最终部署架构

```text
payment backend
  -> RAGClient
  -> http://rag-service:8020

rag-service
  -> /rag/search
  -> /rag/answer
  -> /admin/reindex
  -> Qdrant
  -> Redis
  -> local embedding model
  -> local reranker model
  -> knowledge_base
  -> LLM provider
```

## 3. 责任归属速查

| 问题 | 归属 | 必须处理 |
| --- | --- | --- |
| Qdrant 部署、持久化、snapshot | 服务器/RAG Service | 是 |
| Redis 部署和缓存/job 状态 | 服务器/RAG Service | 是，属于新增服务能力 |
| embedding/reranker 模型下载 | 服务器/RAG Service | 是 |
| 知识库目录挂载和同步 | 服务器/RAG Service | 是 |
| `/rag/search`、`/rag/answer`、`/admin/reindex` | RAG Service | 是 |
| `config_gen_retriever.py` 能力清理/迁移 | RAG Service + payment 调用方 | 是；当前未接生产，默认 experimental |
| `knowledge_qa_tool`、`rag_search_tool` 改造 | payment 后端 | 是 |
| `admin/rag.py` Form 到 JSON 转换 | payment 后端 | 是 |
| `backend/app/rag/__init__.py` 清理 | payment 后端 | 是 |
| `feedback/inject.py` 远程 RAG fallback | payment 后端 | 是 |
| `main.py /ready` 改调 RAG Service | payment 后端 | 是 |
| 远程 HTTP client、超时、鉴权 | payment 后端 | 是 |
| RAG eval baseline 对比 | RAG Service + payment 后端 | 是 |

Redis 不是当前本地 RAG 已有依赖，而是独立服务化后的服务器新增组件。它用于 query embedding 缓存、检索结果缓存、answer 缓存和 reindex job 状态。如果最终实现决定不做缓存和异步 reindex job，才能移除 Redis；否则文档中的 Redis 属于服务器侧必须部署项。

## 4. 服务器目录

```bash
sudo mkdir -p /data/payment-rag/knowledge_base
sudo mkdir -p /data/payment-rag/models/huggingface
sudo mkdir -p /data/payment-rag/qdrant
sudo mkdir -p /data/payment-rag/redis
sudo mkdir -p /data/payment-rag/indexes/bm25
sudo mkdir -p /data/payment-rag/indexes/manifests
sudo mkdir -p /data/payment-rag/logs
sudo mkdir -p /data/payment-rag/snapshots
```

目录用途:

| 路径 | 用途 |
| --- | --- |
| `/data/payment-rag/knowledge_base` | Markdown 知识库运行时挂载目录 |
| `/data/payment-rag/models/huggingface` | embedding/reranker 模型目录 |
| `/data/payment-rag/qdrant` | Qdrant 向量库持久化 |
| `/data/payment-rag/redis` | Redis AOF/RDB 数据 |
| `/data/payment-rag/indexes/bm25` | BM25 倒排索引、词频、文档频率等落盘文件 |
| `/data/payment-rag/indexes/manifests` | chunk/index/model manifest |
| `/data/payment-rag/snapshots` | Qdrant snapshot 备份 |

## 5. Python 依赖

`backend/rag-service/requirements.txt`:

```text
fastapi
uvicorn[standard]
pydantic
pydantic-settings
python-dotenv
pyyaml
qdrant-client
sentence-transformers
transformers
huggingface-hub
torch
rank-bm25
jieba
numpy
scikit-learn
redis
httpx
orjson
tenacity
prometheus-client
```

LangChain 依赖策略:

- RAG Service 不引入 `langchain` 大包。
- Qdrant 使用 `qdrant-client` 原生 SDK。
- embedding 使用 sentence-transformers `SentenceTransformer`。
- rerank 使用 sentence-transformers `CrossEncoder`。
- chunk 数据使用自定义 Pydantic schema。
- 如果短期继续使用 `MarkdownHeaderTextSplitter`，只引入最小依赖，并在代码中标注为迁移债务；不允许继续使用 LangChain vector store 或 compressor。

安装:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r backend/rag-service/requirements.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r backend\rag-service\requirements.txt
```

GPU 服务器需按 CUDA 版本安装匹配 PyTorch。

## 6. 模型下载

服务器提前下载模型，并设置:

```bash
HF_HUB_OFFLINE=1
```

安装 Hugging Face CLI:

```bash
pip install -U huggingface-hub
```

下载 embedding 模型:

```bash
hf download BAAI/bge-small-zh-v1.5 \
  --revision <commit_hash> \
  --local-dir /data/payment-rag/models/huggingface/BAAI/bge-small-zh-v1.5
```

下载 reranker 模型:

```bash
hf download BAAI/bge-reranker-base \
  --revision <commit_hash> \
  --local-dir /data/payment-rag/models/huggingface/BAAI/bge-reranker-base
```

服务加载路径:

```python
SentenceTransformer("/models/huggingface/BAAI/bge-small-zh-v1.5")
CrossEncoder("/models/huggingface/BAAI/bge-reranker-base")
```

注意:

- `bge-small-zh-v1.5` 通常为 512 维。
- 模型必须锁定 revision。不能只记录模型名，否则不同时间下载到的权重可能变化，导致 query embedding 与 Qdrant 已有向量不在同一语义空间。
- 下载后由 `service.scripts.download_models` 或服务器模型下载步骤写入 `/data/payment-rag/indexes/manifests/models.lock.json`，记录 `model_id`、`revision`、本地路径和关键文件 hash；`/admin/reindex` 和服务启动只读取校验该文件，不自动重写。
- 更换 embedding 模型必须新建 collection 或重建索引。
- 同一个 Qdrant collection 不能混用不同维度或不同语义空间的向量。
- 离线部署变量必须是 `HF_HUB_OFFLINE=1`，不要写成 `HF_HUB_OFFSET`。

CPU/GPU 要求:

- GPU 不是功能必需项，RAG Service 可以纯 CPU 部署。
- CPU 部署适合小知识库和低并发，首次全量 reindex 可能需要数分钟；`/rag/search` warm latency 主要受 reranker 影响。
- 有 NVIDIA GPU 时建议安装匹配 CUDA 的 PyTorch，并在 `rag_service.yaml` 中配置 `device: cuda`；无 GPU 时配置 `device: cpu`。

## 7. Qdrant 部署和迁移

Qdrant 是服务器侧新增基础设施，不是当前本地 `InMemoryVectorStore` 的简单替换。迁移必须完成以下事项:

- 服务器部署 Qdrant 容器或托管服务。
- 为每个 embedding 模型维度创建对应 collection。
- 将 Markdown chunk embedding 后写入 Qdrant points。
- 将 chunk metadata 写入 payload。
- 建立 payload index。
- 配置 snapshot/restore。
- RAG Service `/ready` 必须检查 collection 是否存在、维度是否匹配、point 数是否大于 0。

Collection 创建时机:

- RAG Service 启动时 **不自动创建空 collection**，避免配置错误时创建出错误维度或错误命名的 collection。
- `/admin/reindex` 和 `service.scripts.build_index` 负责幂等初始化: 检查 collection 是否存在，不存在则按配置创建；存在则校验 vector size、distance 和 embedding model manifest。
- 首次部署时，启动 Qdrant 和 RAG Service 后，必须先执行 `/admin/reindex` 或 `build_index`，它会完成 collection 创建、payload index 创建、向量写入、BM25 落盘和 manifest 写入。
- 如果 collection 存在但维度或 embedding revision 不匹配，`force=false` 必须失败；`force=true` 才允许重建 collection。

```bash
docker run -d \
  --name payment-rag-qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v /data/payment-rag/qdrant:/qdrant/storage \
  qdrant/qdrant
```

健康检查:

```bash
curl http://localhost:6333/healthz
```

Collection:

```text
bank_stmt_knowledge  # 稳定 alias
```

Qdrant collection 命名和保留策略:

- `domains.<domain>.collection` 是稳定 alias，例如 `bank_stmt_knowledge`。
- 物理 collection 命名为 `<alias>__v<version:06d>__<job_id_short>`，例如 `bank_stmt_knowledge__v000012__a1b2c3d4`。
- `/admin/reindex` 和 `build_index` 写入 candidate 物理 collection，验证成功后才把 alias 切到新 collection。
- payment 后端、RAG API 和运维调试只使用 alias；物理 collection 名只用于 RAG Service 内部发布、回滚和清理。
- 发布成功后旧物理 collection 默认保留最近 `qdrant.keep_versions=2` 个版本，用于回滚；超过数量的旧 collection 由后台清理任务或人工命令删除。
- 构建失败的 candidate collection 必须删除；删除失败只告警，不改变当前 alias。

Vector:

```text
size: 512
distance: Cosine
```

Payload 字段:

必填字段与 [RAG高级检索能力开发指南.md](RAG高级检索能力开发指南.md) 保持一致:

- `chunk_id`
- `parent_id`
- `source`
- `heading`
- `heading_path`
- `content`
- `content_hash`
- `knowledge_version`
- `chunk_version`
- `embedding_model`
- `embedding_revision`
- `prev_chunk_id`
- `next_chunk_id`
- `chunk_type`
- `ordinal`

业务可选字段:

- `category`
- `statement_type`
- `entities`

Payload index:

- `source`
- `heading_path`
- `category`
- `knowledge_version`
- `embedding_model`
- `embedding_revision`
- `statement_type`

BM25 索引持久化:

BM25 不放 Redis。Redis 只保存缓存和 job 状态。BM25 倒排索引、文档频率、chunk 映射等文件必须落盘:

```text
/data/payment-rag/indexes/bm25/bank_stmt/
  bm25.pkl
  chunks.jsonl
  tokenizer.json
  manifest.json
```

这里有两层 manifest:

- `indexes/manifests/<domain>.json`: 顶层 `IndexManifest`，描述整个 domain 的索引版本，包含 Qdrant collection、BM25 namespace、模型 revision、chunk 数、`knowledge_version` 和索引内容 `content_hash`。
- `indexes/bm25/<domain>/manifest.json`: BM25 子索引 manifest，只描述 BM25 文件、分词器、chunk 映射和 BM25 构建输入 hash。

RAG Service cold start 时从该目录加载 BM25。若文件不存在或 manifest 与知识库 hash 不匹配，`bank_stmt` domain 的 `/ready` 返回 `503 starting` 或 `503 unindexed`，不能提前接收生产流量。

BM25 热加载:

- reindex 先构建到临时目录，例如 `/data/payment-rag/indexes/bm25/bank_stmt.tmp/<job_id>/`。
- 构建成功并写完 manifest 后，RetrievalService 先从临时目录加载新 BM25 reader 并执行 smoke search 校验。
- 新 reader 加载成功后，才使用原子 rename/symlink swap 切到新版本并替换内存 reader。
- 构建和校验期间 `/rag/search` 使用旧索引继续服务；进入发布锁窗口后，新请求等待发布完成，超过 `reindex.publish_lock_timeout_seconds` 返回 `503 publishing`，不得混用新旧组件。
- 加载、校验或 swap 任一步失败时保留旧索引，job 标记 `failed`，不得让 current 指向坏目录。

BM25 + Qdrant 发布一致性:

- reindex 发布前必须同时准备好 Qdrant candidate collection、BM25 staging、chunks 内存索引和新 manifest。
- 发布时获取 domain 级发布锁；多 worker 部署必须使用 Redis 或等价分布式锁。
- 发布锁期间新进入的 `/rag/search` 必须等待稳定 generation，超过 `reindex.publish_lock_timeout_seconds` 返回 `503 publishing`。
- 发布顺序为: Qdrant alias swap -> BM25 current swap + reader/chunks 替换 -> 写入顶层 manifest -> ready。
- 如果 Qdrant alias swap 失败，本地 BM25/chunks 不切换。
- 如果 Qdrant alias 已切但 BM25 或 manifest 失败，必须把 alias 回滚到旧 collection，并恢复旧 BM25 current/reader/chunks。
- 如果回滚失败，domain 进入 `503 publish_inconsistent`，`/rag/search` 不继续服务，需人工修复或重新 reindex。

迁移步骤:

1. 停止 payment 后端本地向量索引构建路径。
2. 在 RAG Service 读取知识库并生成 chunk manifest。
3. 使用服务器 embedding 模型生成向量。
4. 通过 `/admin/reindex` 或 `build_index` 创建或重建 Qdrant collection。
5. upsert points。
6. 构建 BM25 index 并写入 `/data/payment-rag/indexes/bm25/<domain>/`。
7. 写入 index manifest。
8. 调用 `/ready` 和 `/rag/search` 验证。

snapshot:

```bash
curl -X POST http://localhost:6333/collections/bank_stmt_knowledge/snapshots
```

## 8. Redis 部署

```bash
docker run -d \
  --name payment-rag-redis \
  -p 6379:6379 \
  -v /data/payment-rag/redis:/data \
  redis:7 redis-server --appendonly yes
```

用途:

- query embedding 缓存。
- search 结果缓存。
- answer 缓存。
- reindex job 状态。
- 多 worker 索引发布通知 pub/sub。

Redis 是服务化后的新增服务器组件。当前本地 RAG 没有 Redis 依赖；引入 Redis 的理由是:

- 避免重复计算 query embedding。
- 避免高频相同问题重复检索和 rerank。
- 保存 `/admin/reindex` 异步 job 状态。
- 支撑多实例 RAG Service 共享缓存和索引发布通知。

多 worker 热加载策略:

- 发布事务成功写入 manifest 后，通过 Redis pub/sub 发布 `rag:index-updated:<domain>`。
- 每个 RAG Service worker 收到消息后加载新的 manifest、BM25 current、chunks 内存索引并校验 Qdrant alias。
- Redis pub/sub 可能丢消息，因此 worker 还必须按 `runtime.manifest_poll_interval_seconds` 轮询 manifest 作为兜底。
- 生产多 worker 部署不能只依赖本地内存状态；任何 worker 加载新版本失败时，该 worker 对对应 domain 返回 `503 reload_failed`。

模型并发策略:

- `SentenceTransformer.encode()` 和 `CrossEncoder.predict()` 必须从 FastAPI event loop 移到受限 executor 执行。
- CPU 默认 `embedding_max_concurrency=1`、`reranker_max_concurrency=1`；放大并发前必须压测 CPU、BLAS 线程和延迟抖动。
- reindex 任务不得抢占所有模型推理并发；资源紧张时 search 优先，reindex 降速。

如果实现时明确取消缓存和异步 reindex job，Redis 才可以从部署中删除。否则它是服务器侧必须解决的组件。

## 9. RAG Service 环境变量

```bash
RAG_SERVICE_HOST=0.0.0.0
RAG_SERVICE_PORT=8020
RAG_API_KEY=change-me

RAG_KNOWLEDGE_DIR=/data/payment-rag/knowledge_base/流水问答
RAG_COLLECTION=bank_stmt_knowledge
RAG_CHUNK_VERSION=v1
RAG_INDEX_DIR=/data/payment-rag/indexes
RAG_BM25_INDEX_DIR=/data/payment-rag/indexes/bm25
RAG_MODEL_LOCK_FILE=/data/payment-rag/indexes/manifests/models.lock.json
RAG_REINDEX_ASYNC=true

RAG_EMBEDDING_MODEL=/models/huggingface/BAAI/bge-small-zh-v1.5
RAG_RERANKER_MODEL=/models/huggingface/BAAI/bge-reranker-base
RAG_EMBEDDING_REVISION=<commit_hash>
RAG_RERANKER_REVISION=<commit_hash>
RAG_EMBEDDING_DEVICE=cpu
RAG_RERANKER_DEVICE=cpu
RAG_MODEL_CACHE_DIR=/models/huggingface
HF_HUB_OFFLINE=1

QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
QDRANT_DISTANCE=Cosine
QDRANT_VECTOR_SIZE=512

REDIS_URL=redis://redis:6379/2

RAG_KEYWORD_BACKEND=bm25
RAG_TOP_K_KEYWORD=20
RAG_TOP_K_VECTOR=40
RAG_TOP_K_RERANK=5
RAG_RRF_K=60

RAG_ENABLE_ANSWER=true
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

LLM 配置归属:

- RAG Service 使用自己的环境变量和 `backend/rag-service/config/rag_service.yaml`。
- payment 后端继续使用 `backend/config/llm.yaml`。
- 两者可以配置为同一个 LLM 网关、同一个模型、同一个 API key，但这是部署层的配置值复用，不是代码层共享 loader。
- RAG Service 不 import `backend.app.llm.chat_model`，避免服务边界反向依赖 payment 后端。
- `/rag/answer` 的 LLM SLA 单独统计，不混入 payment 后端普通 AgentLoop LLM 统计。

`backend/rag-service/config/rag_service.yaml` 是唯一 canonical 配置模板。部署指南和高级检索开发指南都以该文件为准；文档中的 YAML 片段只做说明，不另维护第二套模板。当前模板结构如下:

```yaml
server:
  host: ${RAG_SERVICE_HOST:0.0.0.0}
  port: ${RAG_SERVICE_PORT:8020}
  api_key_env: RAG_API_KEY
  auth_mode: header

paths:
  knowledge_root: /app/knowledge_base
  index_dir: ${RAG_INDEX_DIR:/data/payment-rag/indexes}
  bm25_index_dir: ${RAG_BM25_INDEX_DIR:/data/payment-rag/indexes/bm25}
  model_lock_file: ${RAG_MODEL_LOCK_FILE:/data/payment-rag/indexes/manifests/models.lock.json}

domains:
  bank_stmt:
    enabled: true
    knowledge_dir: /app/knowledge_base/流水问答
    collection: bank_stmt_knowledge
    retrieval_mode: hybrid
    pipeline_profile: hybrid_advanced
    bm25_namespace: bank_stmt
    require_vector: true
    require_reranker: true
    enable_context_enrich: true
  config_gen:
    enabled: false
    experimental: true
    knowledge_dir: /app/knowledge_base/流水配置生成 RAG增强
    retrieval_mode: keyword
    pipeline_profile: keyword_simple
    bm25_namespace: parser_config_knowledge
    require_vector: false
    require_reranker: false
    enable_context_enrich: false

qdrant:
  url: ${QDRANT_URL}
  api_key_env: QDRANT_API_KEY
  vector_size: ${QDRANT_VECTOR_SIZE:512}
  distance: ${QDRANT_DISTANCE:Cosine}
  keep_versions: 2

redis:
  url: ${REDIS_URL}
  enabled: true

models:
  embedding:
    path: ${RAG_EMBEDDING_MODEL}
    model_id: BAAI/bge-small-zh-v1.5
    revision: ${RAG_EMBEDDING_REVISION}
    device: ${RAG_EMBEDDING_DEVICE:cpu}
    semantic_similarity_threshold: 0.62
  reranker:
    path: ${RAG_RERANKER_MODEL}
    model_id: BAAI/bge-reranker-base
    revision: ${RAG_RERANKER_REVISION}
    device: ${RAG_RERANKER_DEVICE:cpu}

retrieval:
  mode: hybrid
  top_k_keyword: ${RAG_TOP_K_KEYWORD:20}
  top_k_vector: ${RAG_TOP_K_VECTOR:40}
  top_k_rerank: ${RAG_TOP_K_RERANK:5}
  rrf_k: ${RAG_RRF_K:60}
  rrf_top_n: 10
  default_top_k: 5
  dedup_jaccard_threshold: 0.8
  allow_rerank_fallback: false
  max_quote_chars: 160

chunking:
  # 当前唯一合法值为 semantic；fixed/recursive 需要新增实现后再开放。
  mode: semantic
  child_max_tokens: 350
  child_min_tokens: 80
  parent_max_tokens: 1200
  overlap_tokens: 40

tokenizers:
  chunking: embedding
  embedding_text: embedding
  rerank_context: reranker
  answer_context: llm

small_to_big:
  enabled: true
  return_parent: true
  max_parent_tokens: 1200

context_enrich:
  enabled: true
  before: 1
  after: 1
  max_context_tokens: 1800

reindex:
  async: ${RAG_REINDEX_ASYNC:true}
  job_ttl_seconds: 86400
  running_timeout_seconds: 1800
  publish_lock_timeout_seconds: 5
  online_strategy: atomic_swap

runtime:
  search_snapshot_mode: rcu
  model_executor: threadpool
  embedding_max_concurrency: 1
  reranker_max_concurrency: 1
  manifest_reload: redis_pubsub_with_polling_fallback
  manifest_poll_interval_seconds: 2

eval:
  enable_debug_api: false

llm:
  base_url: ${LLM_BASE_URL}
  api_key_env: LLM_API_KEY
  model: ${LLM_MODEL}
  timeout_seconds: 60
```

`tokenizers.*` 的值是来源别名，不是模型名；`embedding` 映射到 `models.embedding`，`reranker` 映射到 `models.reranker`，`llm` 映射到 `llm` 配置段。rerank 版上下文按 reranker tokenizer 和 `models.reranker.max_length` 截断，answer 版上下文在 rerank 后重新回表组装，按 LLM tokenizer 和 `context_enrich.max_context_tokens` 截断。

## 10. 知识库归属

仓库内 `backend/knowledge_base` 是源文件目录。服务化后，运行时知识库归属 RAG Service，服务器挂载为:

```text
/data/payment-rag/knowledge_base/流水问答
/data/payment-rag/knowledge_base/流水配置生成 RAG增强
```

RAG Service 负责读取这两个目录:

- `流水问答`: 面向 `knowledge_qa`、`rag_search`、`/rag/search`、`/rag/answer`。
- `流水配置生成 RAG增强`: 面向配置生成链路，对应原 `config_gen_retriever.py`。

payment 后端不再在运行期直接读取上述 Markdown 做 RAG 检索。部署时从仓库同步源文件到服务器目录:

```bash
rsync -av backend/knowledge_base/ user@server:/data/payment-rag/knowledge_base/
```

两个知识库隔离:

| domain | 目录 | collection/namespace | 检索方式 | readiness |
| --- | --- | --- | --- | --- |
| `bank_stmt` | `流水问答` | `bank_stmt_knowledge` | hybrid: BM25 + vector + RRF + rerank | 依赖 Qdrant、embedding、reranker、BM25 |
| `config_gen` | `流水配置生成 RAG增强` | `parser_config_knowledge` BM25 namespace，不入 Qdrant | keyword-only | 只依赖 Markdown/BM25 |

`config_gen` 当前未接生产链路，默认按 experimental domain 管理。`bank_stmt` 模型加载失败时，`config_gen` 的 keyword-only API 仍应可用。`/ready` 需要分别报告:

```json
{
  "domains": {
    "bank_stmt": {"status": "ok"},
    "config_gen": {"status": "ok"}
  }
}
```

RAG Service `/ready` 状态机:

| 阶段 | HTTP | body status | 说明 |
| --- | --- | --- | --- |
| 进程启动但模型未加载 | 503 | `starting` | embedding/reranker/LLM client 尚未预热完成 |
| Qdrant 或 Redis 不可连接 | 503 | `dependency_unavailable` | 基础设施不可用，不接收流量 |
| embedding/reranker 必需模型不可用 | 503 | `model_unavailable` | `bank_stmt` 依赖的模型未加载或推理失败 |
| chunking/profile/domain 配置不兼容 | 503 | `invalid_config` | 例如 `overlap_tokens >= child_min_tokens` 或 `pipeline_profile` 与 `retrieval_mode` 冲突 |
| collection 不存在或 point 数为 0 | 503 | `unindexed` | 首次部署尚未执行 `/admin/reindex` |
| BM25 index 缺失或 manifest 不匹配 | 503 | `unindexed` | 需要重建 BM25/index manifest |
| experimental `config_gen` 未启用 | 200 | `ok` | 不影响生产 `bank_stmt` readiness |
| `bank_stmt` 全部依赖可用 | 200 | `ok` | 可以接收生产流量 |

`/health` 只表示 HTTP 进程存活，可以在模型加载中返回 200；`/ready` 才用于生产流量切换。

## 11. Dockerfile

`backend/rag-service/docker/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY service /app/service
COPY config /app/config
COPY evals /app/evals

EXPOSE 8020

VOLUME ["/models/huggingface", "/app/knowledge_base", "/app/indexes"]

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8020"]
```

单独使用 Dockerfile 时，模型、知识库和索引目录必须外部挂载；镜像内不内置 HuggingFace 模型和生产知识库。

## 12. Docker Compose

`backend/rag-service/docker/docker-compose.yml`:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    container_name: payment-rag-qdrant
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - /data/payment-rag/qdrant:/qdrant/storage
    restart: unless-stopped

  redis:
    image: redis:7
    container_name: payment-rag-redis
    command: ["redis-server", "--appendonly", "yes"]
    ports:
      - "6379:6379"
    volumes:
      - /data/payment-rag/redis:/data
    restart: unless-stopped

  rag-service:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: payment-rag-service
    environment:
      RAG_SERVICE_PORT: "8020"
      RAG_KNOWLEDGE_DIR: /app/knowledge_base/流水问答
      RAG_COLLECTION: bank_stmt_knowledge
      RAG_INDEX_DIR: /app/indexes
      RAG_BM25_INDEX_DIR: /app/indexes/bm25
      RAG_MODEL_LOCK_FILE: /app/indexes/manifests/models.lock.json
      RAG_EMBEDDING_MODEL: /models/huggingface/BAAI/bge-small-zh-v1.5
      RAG_RERANKER_MODEL: /models/huggingface/BAAI/bge-reranker-base
      RAG_EMBEDDING_REVISION: "<commit_hash>"
      RAG_RERANKER_REVISION: "<commit_hash>"
      RAG_MODEL_CACHE_DIR: /models/huggingface
      HF_HUB_OFFLINE: "1"
      QDRANT_URL: http://qdrant:6333
      REDIS_URL: redis://redis:6379/2
      RAG_API_KEY: change-me
      RAG_ENABLE_ANSWER: "true"
      RAG_REINDEX_ASYNC: "true"
    volumes:
      - /data/payment-rag/knowledge_base:/app/knowledge_base:ro
      - /data/payment-rag/models/huggingface:/models/huggingface:ro
      - /data/payment-rag/indexes:/app/indexes
      - /data/payment-rag/logs:/app/logs
    ports:
      - "8020:8020"
    depends_on:
      - qdrant
      - redis
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8020/health"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 60s
    restart: unless-stopped
```

Docker `running` 只代表进程已启动，不代表模型、Qdrant collection、BM25 index 已 ready。服务流量切换必须以 `/ready` 为准；`healthcheck` 用于发现进程级异常和基础 HTTP 存活。

启动:

```bash
cd backend/rag-service/docker
docker compose up -d --build
```

首次部署 checklist:

1. 创建 `/data/payment-rag/*` 服务器目录。
2. 下载并锁定 embedding/reranker 模型 revision，写入 `models.lock.json`。
3. 同步 `backend/knowledge_base/` 到 `/data/payment-rag/knowledge_base/`。
4. 启动 Qdrant 和 Redis。
5. 启动 RAG Service。
6. 调用 `/health` 确认 HTTP 进程存活。
7. 调用 `/admin/reindex`，等待 job 成功；该步骤负责创建 Qdrant collection、payload index、向量 points、BM25 index 和 manifest。
8. 调用 `/ready`，必须返回 `200 ok` 后才允许 payment 后端切流。
9. 调用 `/rag/search`、`/rag/answer` 和远程 eval 做上线验收。

## 13. 本地启动

```bash
cd backend/rag-service
uvicorn service.main:app --host 0.0.0.0 --port 8020
```

健康检查:

```bash
curl http://localhost:8020/health
curl http://localhost:8020/ready
```

## 14. 知识库同步和索引构建

同步:

```bash
rsync -av backend/knowledge_base/ user@server:/data/payment-rag/knowledge_base/
```

重建索引采用异步 job。`POST /admin/reindex` 只负责提交任务，任务状态保存在 Redis，耗时的 chunk、embedding、Qdrant upsert、BM25 构建在 RAG Service 后台执行。

reindex 是按 domain 隔离执行的。`bank_stmt` reindex 失败不能影响 `config_gen` keyword-only namespace；`config_gen` 不依赖 embedding/reranker，因此不会因为 `bank_stmt` 模型失败而无法构建自己的 BM25 index。

在线搜索行为:

- 默认策略为 `reindex.online_strategy=atomic_swap`。
- reindex 运行期间 `/rag/search` 继续使用旧的可用索引。
- 构建成功后通过发布事务统一切换 Qdrant alias、BM25 current、reader/chunks 内存索引和顶层 manifest。
- 发布锁窗口内新请求等待稳定 generation，超过 `reindex.publish_lock_timeout_seconds` 返回 `503 publishing`。
- 构建失败时旧索引继续服务，job 状态为 `failed`。
- 只有首次部署无旧索引时，`/ready` 返回 `503 unindexed`，`/rag/search` 返回 503。

```bash
curl -X POST http://localhost:8020/admin/reindex \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"domain":"bank_stmt","collection":"bank_stmt_knowledge","force":true}'
```

返回:

```json
{
  "job_id": "rag-reindex-20260708-001",
  "status": "queued",
  "status_url": "/admin/reindex/rag-reindex-20260708-001"
}
```

轮询:

```bash
curl -H "X-API-Key: change-me" \
  http://localhost:8020/admin/reindex/rag-reindex-20260708-001
```

成功状态:

```json
{
  "job_id": "rag-reindex-20260708-001",
  "status": "succeeded",
  "result": {
    "collection": "bank_stmt_knowledge",
    "points": 180,
    "bm25_index": "/app/indexes/bm25/bank_stmt/manifest.json"
  }
}
```

失败状态:

```json
{
  "job_id": "rag-reindex-20260708-001",
  "status": "failed",
  "error": "Qdrant upsert timeout after 30s",
  "retry_allowed": true
}
```

job 失败后不自动重试。需要人工查看 RAG Service 日志、Qdrant/Redis 状态和模型加载错误后，再重新提交 `/admin/reindex`。

`/admin/reindex` 必须幂等处理 collection 创建: collection 不存在时创建，存在时校验 schema；BM25 index 不存在时构建，存在但 manifest 不匹配时重建。

空知识库处理:

- reindex 后如果有效 `chunk_count=0`，job 必须失败，错误码 `empty_knowledge`。
- 不写新 manifest、不自增 version、不切 Qdrant alias、不切 BM25 current。
- 如果存在旧索引，旧索引继续服务；如果没有旧索引，`/ready` 保持 `503 unindexed`。
- `/rag/search` 空结果只表示检索成功但无命中，不表示空知识库或索引未构建。

脚本方式:

```bash
cd backend/rag-service
python -m service.scripts.build_index \
  --knowledge-dir /data/payment-rag/knowledge_base/流水问答 \
  --collection bank_stmt_knowledge \
  --embedding-model /data/payment-rag/models/huggingface/BAAI/bge-small-zh-v1.5 \
  --force
```

`service/scripts/build_index.py` 是必须实现的离线索引入口。若仓库中该文件仍缺失，表示部署前置任务未完成，不能按脚本方式上线。

配置生成知识库索引属于 experimental。本次生产部署不要求构建，只有启用 `config_gen` domain 或后续接入配置生成链路时才执行:

```bash
curl -X POST http://localhost:8020/admin/reindex \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"domain":"config_gen","namespace":"parser_config_knowledge","force":true}'
```

如果本次不接入 `config_gen` 生产链路，该 BM25 namespace 可以只在 experimental 环境构建；生产 readiness 不应因为 experimental config_gen 未启用而失败。

## 15. 验证检索和问答

检索:

```bash
curl -X POST http://localhost:8020/rag/search \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"query":"交易金额字段是什么意思","top_k":3,"domain":"bank_stmt"}'
```

问答:

```bash
curl -X POST http://localhost:8020/rag/answer \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"question":"发生额和交易金额一样吗","top_k":5,"domain":"bank_stmt","with_citations":true}'
```

配置生成检索为 experimental 验证项。本次生产不启用 `config_gen` 时不执行:

```bash
curl -X POST http://localhost:8020/rag/config-gen/repair-strategies \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"error_codes":["CONFIG_CORE_FIELD_MISSING"],"source_nodes":["field_mapping"],"top_k":5}'
```

## 16. payment 后端对接

payment 后端 `.env`:

```bash
RAG_REMOTE_ENABLED=true
RAG_SERVICE_URL=http://rag-server:8020
RAG_SERVICE_API_KEY=change-me
RAG_REMOTE_TIMEOUT_SECONDS=20
```

过渡开关:

```bash
RAG_REMOTE_ENABLED=true
```

灰度期间可以临时设为 `false` 回到旧路径。最终上线状态必须为 `true`，并且 payment 后端不再加载本地 RAG 模型和向量索引。

调用关系:

```text
knowledge_qa_tool
  -> backend.app.rag.client.RemoteRAGClient.answer()
  -> POST /rag/answer

rag_search_tool
  -> backend.app.rag.client.RemoteRAGClient.search()
  -> POST /rag/search

admin rag search
  -> RemoteRAGClient.search()
  -> POST /rag/search
```

HTTP 客户端选型:

- 必须使用 `httpx.AsyncClient`。
- 不得在 async tool 内使用 `requests`。
- 使用连接池和统一 timeout。

建议:

```python
httpx.Limits(max_connections=50, max_keepalive_connections=20)

SEARCH_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
ANSWER_TIMEOUT = httpx.Timeout(connect=3.0, read=60.0, write=5.0, pool=3.0)
ADMIN_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)
```

`/rag/search` 不经过 LLM，读超时应短。`/rag/answer` 会等待 LLM 生成，默认读超时 60 秒，与当前 `knowledge_qa_tool.timeout = 60` 对齐。`/admin/reindex` 只提交和轮询异步 job，HTTP 调用应快速返回；全量索引构建不占用客户端长连接。

Tool 到 API 映射:

| Tool | RAG API | 说明 |
| --- | --- | --- |
| `knowledge_qa` | `/rag/answer` | `question/top_k/domain` |
| `rag_search` | `/rag/search` | `query/domain/collection/top_k/mode/filters` |
| `three_agent_config_gen` | 暂不接线 | 当前生产代码未使用 `config_gen_retriever.py`；接入另立任务 |
| `admin/rag/search` | `/rag/search` | Form 转 JSON 或改为 JSON |

`domain -> collection` 默认映射:

```text
bank_stmt -> bank_stmt_knowledge Qdrant collection
config_gen -> parser_config_knowledge BM25 namespace
```

异常处理:

- `rag_search_tool.py` 不能静默 `except Exception: return []`。
- RAG Service 超时、5xx、鉴权失败、JSON 解析失败时抛出 `RAGServiceUnavailable`。
- 空结果只代表检索成功但无命中。
- `knowledge_qa_tool.contextualize_result` 清理 `response` 分支，只保留远程 API 的 `answer`。
- `backend/app/rag/__init__.py` 不再导出本地 `retrieve`、`get_index_info`。
- `feedback/inject.py` 删除 `_rag_search_feedback()`；S4 memory injection 不再把知识库 chunk 包装成 `FeedbackMemory(confidence=0.6)`。
- payment `/ready` 改调 RAG Service `/ready`；不再 import `backend.app.rag.vector_store`。

异常分层:

| 异常 | 场景 | 重试 |
| --- | --- | --- |
| `RAGServiceUnavailable` | 连接拒绝、DNS、服务未启动 | 不重试或短重试一次 |
| `RAGServiceTimeout` | 连接/读取/写入/连接池超时 | 可短重试一次 |
| `RAGBadRequest` | 400/422 参数错误 | 不重试 |
| `RAGAuthError` | 401/403 | 不重试 |
| `RAGRemoteError` | 5xx | 可短重试一次 |
| `RAGResponseError` | JSON 解析失败/字段缺失 | 不重试或短重试一次 |

状态格式边界:

- RAG Service 可以返回丰富 JSON。
- Tool 进入 `state["rag_results"]` 前必须做上下文归一化。
- ContextAssembler 只消费归一化后的 `content`。
- 远程 API 新增字段不能直接泄漏到 L4，避免 prompt 结构不稳定。

payment `/ready` 行为:

```text
RAG Service /ready ok      -> checks.rag_service = ok
RAG Service /ready error   -> status = degraded, checks.rag_service = error
RAG Service connection err -> status = degraded, checks.rag_service = unavailable
```

## 17. 安全

- RAG Service 必须启用 API key 或内网 mTLS。
- `server.auth_mode=none` 只允许本地开发；生产必须使用 `auth_mode=header`，或由内网 mTLS/API 网关提供等价鉴权。
- Qdrant 不暴露公网。
- Redis 不暴露公网。
- 模型目录只读挂载。
- 知识库目录只读挂载。
- 日志不打印原始 query、交易明细、账号、身份证、手机号。
- `/admin/reindex` 仅允许管理网络或管理员 token。
- `/rag/eval/search` 仅限测试/内网调用，生产必须通过反向代理或中间件拦截，不得暴露到公网。
- `.env`、API key、LLM key 不进入 git。

## 18. 观测

指标:

- `rag_search_latency_ms`
- `rag_answer_latency_ms`
- `rag_embedding_latency_ms`
- `rag_keyword_latency_ms`
- `rag_vector_latency_ms`
- `rag_rerank_latency_ms`
- `rag_search_count`
- `rag_search_error_count`
- `rag_empty_result_count`
- `rag_collection_points`
- `rag_model_loaded`

日志字段:

```json
{
  "request_id": "...",
  "query_hash": "...",
  "domain": "bank_stmt",
  "collection": "bank_stmt_knowledge",
  "mode": "hybrid",
  "top_k": 5,
  "latency_ms": 86,
  "result_count": 5
}
```

## 19. 回滚

RAG 拆出后不通过 payment 本地 RAG 降级路径回滚。回滚通过部署版本完成:

```text
payment backend previous image
rag-service previous image
qdrant snapshot restore
```

灰度迁移期间，允许通过 `RAG_REMOTE_ENABLED=false` 回到旧路径；最终生产状态必须使用远程 RAG Service。正式移除本地执行路径前，必须至少保留一个可回滚镜像和一份 Qdrant snapshot。

## 20. 测试和验收

集成测试:

- `/ready` 检查 Qdrant、Redis、模型、collection。
- `/ready` 分别报告 `bank_stmt` 和 `config_gen` domain。
- `/rag/search` 返回结果。
- `/rag/answer` 返回带引用答案。
- `/rag/eval/search` 返回 keyword/vector/hybrid 三路结果。
- `/rag/config-gen/repair-strategies` 在 experimental 启用时返回配置修复知识。
- `/admin/reindex` 可重建索引。
- payment `knowledge_qa_tool` 远程调用成功。
- payment `rag_search_tool` 远程调用成功。
- payment `feedback/inject.py` 不再导入本地 retriever。
- payment `/ready` 不再导入本地 vector store。
- RAG Service 不可用时 AgentLoop 不崩溃。

回归评测:

```bash
python -m backend.app.evals.run_rag_eval --remote-url http://rag-server:8020
```

远程 eval 必须通过 RAG Service 专用 eval API 获取分模式结果:

```text
POST /rag/eval/search
  -> keyword results
  -> vector results
  -> hybrid results
```

不能再访问 `HybridRetriever` 的私有方法或属性。若不提供 `/rag/eval/search`，则必须放弃 `keyword_recall_3` 和 `vector_recall_3`，只验收端到端 hybrid/answer 指标。

当前本地 baseline:

```json
{
  "keyword_recall_3": 0.9444,
  "vector_recall_3": 0.9444,
  "hybrid_recall_3": 0.9444,
  "source_coverage": 1.0,
  "refusal_accuracy": 0.5,
  "answer_rate": 0.9444
}
```

baseline 和 dataset 归属:

- canonical dataset: `backend/rag-service/evals/datasets/rag_cases.jsonl`
- canonical baseline: `backend/rag-service/evals/baselines/rag_baseline.json`
- payment 后端不维护第二份 RAG baseline。
- payment CI 如需质量门禁，通过远程 RAG Service URL 调用 canonical eval。

远程 RAG 验收不得低于上述 baseline。性能验收:

- `/rag/search` warm p95 小于 500ms，按单个 domain 统计，不含 LLM answer，且要求 chunks/BM25 reader 已在内存、Qdrant 连接已建立、reranker 已预热。
- `/rag/answer` p95 单独统计，目标由 LLM 服务 SLA 决定。
- BM25 cold load 小于 3s，按单个 domain 统计。
- `/ready` 必须在模型预热、Qdrant collection、Redis、BM25 index 全部可用后返回 ok。

最终验收:

- RAG Service 可以独立启动。
- RAG Service 可以部署到服务器。
- RAG Service 可以连接服务器 Qdrant、Redis、模型目录和知识库目录。
- payment 后端只通过远程 RAG Service 调用检索和问答。
- payment 后端不再加载 embedding/reranker。
- payment 后端不再构建本地向量索引。
