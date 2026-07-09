# RAG 高级检索能力开发指南

创建日期: 2026-07-08

## 1. 目标

本文基于已经独立出来的 `backend/rag-service`，定义必须保留的高级 RAG 检索能力如何开发。

这些能力的定位不是再造一个 Agent，而是让 RAG Service 提供稳定、可解释、可追溯的知识检索和证据返回能力。payment 后端仍负责 Planner、AgentLoop、工具路由、任务拆解、业务决策和最终对话编排。

重要边界:

- 本文描述的是迁移后 RAG Service 需要补齐的目标能力，不代表当前代码已经全部实现。
- 当前 `backend/rag-service` 仍是过渡实现，已有基础 hybrid 检索骨架，但 schema、索引构建、manifest、父子块、邻域增强和 citation 仍需按本文补齐。
- 后续开发必须先完成 schema 对齐，再开发 chunk、index、retrieval pipeline；不能在旧 `dict` 结构上继续堆功能。

## 2. 本文范围

本文覆盖以下能力:

| 能力 | 是否进入 RAG Service | 是否需要 LLM | 当前状态 |
| --- | --- | --- | --- |
| Semantic Chunking | 是 | 否，需要 embedding 模型 | 待实现 |
| Small-to-Big 父子块 | 是 | 否 | 待实现 |
| Context Enrich 邻域增强 | 是 | 否 | 待实现 |
| Header Metadata Chunk | 是 | 否 | 部分实现，需补 frontmatter、heading_path、表格/代码块边界 |
| Hybrid BM25 + Vector | 是 | 否 | 基础实现，需补统一 schema、score_breakdown、manifest 校验 |
| RRF 融合 | 是 | 否 | 基础实现，需保留每路 rank/score |
| CrossEncoder Rerank | 是 | 否，需要 reranker 模型 | 基础实现，需改失败处理，禁止静默降级 |
| Citation Builder | 是 | 否 | 待实现独立模块 |
| Index Manifest/版本管理 | 是 | 否 | 待实现 |
| Eval API | 是，仅内网/测试 | 否 | 待实现或待补齐 |

本文不覆盖以下能力，原因是 payment Agent 已经承担或应另立专项:

| 能力 | 本次不放进 RAG Service 的原因 |
| --- | --- |
| Query Router | payment S0 Planner/AgentLoop 已负责 tool/domain 路由 |
| Query Decomposition | 多步任务拆解是 Agent 编排职责 |
| CRAG 自反思 | 证据不足、是否追问/拒答由 Agent 更适合判断 |
| Web-CRAG | 银行流水业务暂不需要开放域联网检索，且有合规风险 |
| GraphRAG | 面向配置影响分析、多跳追因时另立知识图谱专项 |
| LLM Judge Eval | 放 CI/eval 脚本，不进入在线检索链路 |

## 3. 当前实现差距

以下差距必须作为开发任务处理，不能只停留在文档目标态:

| 类别 | 当前问题 | 目标要求 |
| --- | --- | --- |
| Schema | `service/schemas/rag.py` 缺 `Chunk`、`IndexManifest`，`SearchResult`/`Citation` 字段不足 | 先补齐 Pydantic schema，所有模块统一使用 |
| heading 类型 | 当前多处为 `heading: str` | 内部主模型使用 `heading_path: list[str]`，API 可额外兼容 `heading` 展示字段 |
| 缺失模块 | `markdown_parser.py`、`metadata.py`、`semantic_chunker.py`、`parent_child.py`、`neighbor.py`、`manifest.py`、`context_enrich.py`、`manifest_store.py` 未实现 | 按模块布局补齐 |
| 检索流水线 | 当前大致为 keyword -> vector -> dedup -> RRF -> rerank | 必须补 context_enrich -> citation -> normalize |
| 配置 | 当前 `keyword_recall_top=10`、`vector_recall_top=20`，缺 chunking/small_to_big/context_enrich/eval 和 `retrieval.max_quote_chars` | 统一为 `top_k_keyword=20`、`top_k_vector=40`，补齐配置段 |
| reindex | 当前只是清缓存或标记刷新 | 必须执行完整索引构建 pipeline 并写 manifest |
| reranker 失败 | 当前可能返回 `hybrid(rerank_failed)` 静默降级 | 运行中失败必须显式抛错，`/ready` 不得为 ok |
| chunk_id | 当前形如 `rel_path#i`，不包含内容 hash | 按 §6.1 的 JSON 序列化和固定分隔符规范稳定生成 |
| BM25 | 当前纯内存 | 必须支持磁盘持久化和 cold start 加载 |
| Qdrant payload | 当前 Qdrant store 仍是接口桩或 payload 不完整 | payload 必须写入模型、版本、hash、parent/neighbor 相关字段 |

硬性开发原则:

1. `service/schemas/rag.py` 是最高优先级，必须先改。
2. schema 改完后才能改 loader/chunker/index/retrieval，避免新旧字段继续分裂。
3. 任何失败都要显式错误化，不能用空结果或特殊 retriever 字符串掩盖。

## 4. 目标架构

```text
Markdown Knowledge Base
  -> Loader
  -> Header Metadata Parser
  -> Semantic Chunker
  -> Parent/Child Builder
  -> Neighbor Linker
  -> Index Manifest Writer
  -> BM25 Index
  -> Embedding Model
  -> Qdrant

Query
  -> BM25 Recall
  -> Vector Recall
  -> RRF Fusion
  -> Parent/Neighbor Context Enrich
  -> CrossEncoder Rerank
  -> Citation Builder
  -> /rag/search results
  -> optional /rag/answer LLM answer
```

## 5. 模块布局

在 `backend/rag-service/service/` 下扩展或补齐以下模块:

```text
knowledge/
  loader.py
  markdown_parser.py
  metadata.py
  semantic_chunker.py
  parent_child.py
  neighbor.py
  manifest.py

retrieval/
  keyword.py
  vector.py
  fusion.py
  context_enrich.py
  rerank.py
  citation.py
  service.py

storage/
  qdrant.py
  bm25.py
  manifest_store.py

models/
  embedding.py
  reranker.py

evals/
  runner.py
  metrics.py
```

当前缺失模块必须补齐:

| 模块 | 用途 |
| --- | --- |
| `knowledge/loader.py` | 文件 IO 和 ingestion 编排；输入为 domain knowledge_dir，输出为 MarkdownFile 列表或完整 chunk pipeline 入口 |
| `knowledge/markdown_parser.py` | frontmatter、1-6 级标题、表格/代码块边界解析 |
| `knowledge/metadata.py` | 章节路径、frontmatter 结构化提取 |
| `knowledge/semantic_chunker.py` | embedding 相似度语义切分 |
| `knowledge/parent_child.py` | 父子块构建与映射 |
| `knowledge/neighbor.py` | 前后邻域链接 |
| `knowledge/manifest.py` | `IndexManifest` 生成和校验 |
| `retrieval/context_enrich.py` | 命中后补 parent 和 neighbor 上下文 |
| `retrieval/citation.py` | 独立 citation 构造 |
| `storage/manifest_store.py` | manifest JSON 持久化 |

知识库解析调用链固定为:

```text
loader.py
  -> 读取 Markdown 文件，生成 MarkdownFile(path, relative_path, raw_text)
  -> markdown_parser.py
       输入 raw_text
       输出 ParsedMarkdown(frontmatter_raw, blocks, heading_events, table/code_block ranges)
  -> metadata.py
       输入 relative_path + ParsedMarkdown
       输出 frontmatter dict、heading_path、source/category/statement_type 等结构化 metadata
  -> parent_child.py / semantic_chunker.py / neighbor.py
```

`loader.py` 不直接解析 Markdown 语义；`markdown_parser.py` 不生成业务 metadata；`metadata.py` 不读文件。

空正文文件处理:

- Markdown 剥离 frontmatter 后，如果正文为空字符串或仅包含空白字符，该文件不生成 chunk。
- frontmatter-only Markdown 文件只记录 warning 日志，保留文件路径和原因，不能进入 BM25、Qdrant 或 embedding。
- content 为空的 chunk 必须丢弃，不计算 `content_hash`，不生成 `chunk_id`，不计入 `IndexManifest.chunk_count`。
- 如果某个文件所有块都被丢弃，不影响同一 domain 的其他文件继续构建。

## 6. 核心数据模型

### 6.1 Chunk

```python
class Chunk(BaseModel):
    chunk_id: str
    parent_id: str | None
    source: str
    heading_path: list[str]
    title: str | None
    content: str
    embedding_text: str | None
    content_hash: str
    chunk_type: Literal["parent", "child"]
    ordinal: int
    prev_chunk_id: str | None
    next_chunk_id: str | None
    metadata: dict[str, Any]
```

要求:

- `chunk_id` 必须稳定，基于 `source + heading_path + ordinal + content_hash` 生成，但禁止无分隔符直接拼接。
- `content` 永远存原始正文，不包含标题前缀、来源前缀、同义词 tags 或 embedding 增强文本。
- `embedding_text` 是可选派生字段，只供 embedding/reindex 使用；禁止进入任何 API response、LLM context、Agent L4 context 或 Qdrant payload。
- `content_hash` 使用规范化正文计算，例如 `sha256(normalized_content)`。
- 旧格式 `f"{rel_path}#{i}"` 只能作为迁移前兼容字段，不得作为新索引主键。
- `parent_id` 指向父块，供 Small-to-Big 使用。
- `ordinal` 的计数范围固定为同一个 `parent_id` 下的 child 顺序，从 1 开始递增；parent chunk 自身的 `ordinal` 为 0。
- `prev_chunk_id`、`next_chunk_id` 供邻域增强使用。
- `heading_path` 保留 Markdown 层级标题。

`title` 生成规则:

1. 优先使用 frontmatter 中的 `title` 字段。
2. 没有 frontmatter title 时，使用当前 chunk 所属的最深 Markdown 标题，即 `heading_path[-1]`。
3. `heading_path` 为空时，使用源文件名 stem。
4. parent chunk 的 `title` 是 parent 所在章节标题；child chunk 的 `title` 默认继承其所属 parent title，除非 child 自身位于更深层标题下。
5. `title` 只用于展示、BM25 索引增强和 citation 标题，不参与 `content_hash` 计算。

`chunk_id` 生成规范:

```python
payload = [
    source,
    json.dumps(heading_path, ensure_ascii=False, separators=(",", ":")),
    str(ordinal),
    content_hash,
]
chunk_id = "sha256:" + sha256("||".join(payload).encode("utf-8")).hexdigest()
```

`heading_path` 必须用 JSON array 序列化，不能用 `"/".join(heading_path)`；分隔符固定为 `||`。这样可以避免 `source="a/b", heading_path=["c"]` 与 `source="a", heading_path=["b/c"]` 这类边界碰撞。

### 6.2 SearchResult

```python
class SearchResult(BaseModel):
    chunk_id: str
    parent_id: str | None
    source: str
    heading: str | None
    heading_path: list[str]
    content: str
    score: float
    score_breakdown: dict[str, float | None]
    retriever: Literal["keyword", "vector", "hybrid"]
    citations: list[Citation]
    metadata: dict[str, Any]
```

`score_breakdown` 至少包含:

```json
{
  "keyword_score": 0.71,
  "vector_score": 0.83,
  "rrf_score": 0.49,
  "rerank_score": 0.91,
  "final_score": 0.9281
}
```

非 hybrid 模式下也返回同一组 key，未参与的阶段填 `null`，不要填 `0.0` 伪装成真实分数。例如 keyword-only:

```json
{
  "keyword_score": 0.71,
  "vector_score": null,
  "rrf_score": null,
  "rerank_score": null,
  "final_score": 0.71
}
```

### 6.3 Citation

```python
class Citation(BaseModel):
    index: int
    source: str
    heading: str | None
    heading_path: list[str]
    chunk_id: str
    parent_id: str | None
    content_hash: str
    quote: str | None
```

旧 citation 只有 `index/source/heading`，迁移后必须补齐 `heading_path/chunk_id/parent_id/content_hash/quote`。`heading` 只作为展示字段保留，内部定位以 `heading_path` 为准。

### 6.4 IndexManifest

```python
class IndexManifest(BaseModel):
    version: int
    domain: str
    collection: str | None
    bm25_namespace: str
    knowledge_version: str
    chunk_version: str
    embedding_model_id: str | None
    embedding_revision: str | None
    vector_size: int | None
    distance: str | None
    chunk_count: int
    parent_count: int
    child_count: int
    content_hash: str
    created_at: datetime
```

manifest 写入:

```text
/data/payment-rag/indexes/manifests/<domain>.json
```

本地开发默认路径:

```text
backend/rag-service/.local/indexes/manifests/<domain>.json
```

`IndexManifest` 是 domain 顶层 manifest。BM25 目录下的 `manifest.json` 是 BM25 子索引 manifest，二者不是同一个文件:

- 顶层 `IndexManifest`: 描述 domain 整体索引，包括 Qdrant collection、BM25 namespace、embedding revision、chunk 数、`knowledge_version` 和索引内容 `content_hash`。
- BM25 `manifest.json`: 描述 BM25 文件、分词器、chunk 映射和 BM25 构建输入 hash。

`knowledge_version` 生成规则:

- 默认由知识库内容 fingerprint 生成，不要求人工传入。
- fingerprint 输入为该 domain 下所有 Markdown 文件的相对路径、文件内容 hash、文件大小和 mtime 的稳定排序列表。
- 若部署环境提供 `RAG_KNOWLEDGE_VERSION` 或 `/admin/reindex` 显式传入 `knowledge_version`，则记录为 `external_version`，但仍必须保存内容 fingerprint。
- manifest 校验以内容 fingerprint 为准，外部版本只用于发布追踪和日志展示。

`version` 是单调递增的 domain 索引 generation:

- 每个 domain 独立维护自己的 `version`。
- 每次 reindex 成功并完成 atomic swap 后自增 1。
- reindex 失败不增加 version。
- RetrievalService 通过 `IndexManifest.version` 判断是否需要热加载 BM25 reader 或刷新 collection alias。

`content_hash` 是索引内容完整性 hash，不等同于 `knowledge_version`:

- 输入为所有 chunk 的 `(chunk_id, content_hash)` 二元组。
- 先按 `chunk_id` 字典序稳定排序。
- 每行序列化为 `chunk_id + "\t" + content_hash`，行之间用 `\n` 连接。
- 最终值为 `sha256(joined_lines.encode("utf-8"))`，建议带 `sha256:` 前缀。
- 它用于检测实际索引 chunk 集合是否变化；`knowledge_version` 用于追踪源知识库 fingerprint 或外部发布版本。

### 6.5 ActiveIndexSnapshot

`ActiveIndexSnapshot` 是 RetrievalService 运行期的不可变快照，不写入 API response，也不作为 manifest 落盘。所有 `/rag/search` 请求只在开始时读取一次 snapshot 引用，并在本次请求内一直使用该对象。

```python
@dataclass(frozen=True)
class ActiveIndexSnapshot:
    domain: str
    version: int
    pipeline_profile: Literal["hybrid_advanced", "keyword_simple"]
    retrieval_mode: Literal["hybrid", "keyword", "vector"]
    qdrant_alias: str | None
    qdrant_collection: str | None
    bm25_namespace: str
    bm25_current_dir: str
    bm25_manifest: dict[str, Any]
    bm25_reader: Any
    chunks_by_id: Mapping[str, Chunk]
    manifest: IndexManifest
    ready_status: str
    loaded_at: datetime
```

字段说明:

- `qdrant_alias` 是稳定逻辑名，例如 `bank_stmt_knowledge`；`qdrant_collection` 是 alias 当前指向的物理 collection。
- `bm25_reader` 是已加载并 smoke test 通过的 reader 实例，禁止在 search 中重新从磁盘加载。
- `chunks_by_id` 是从当前版本 `chunks.jsonl` 加载出的内存索引，必须视为只读。
- `ready_status` 只能是当前 snapshot 对应 domain 的状态；若为 `ok` 以外状态，search 入口必须按同名错误返回 503。
- 发布事务只能用完整的新 `ActiveIndexSnapshot` 替换旧 snapshot 引用，不能局部替换 `bm25_reader` 或 `chunks_by_id`。

## 7. 索引构建流程

`/admin/reindex` 和 `service.scripts.build_index` 共用同一套 pipeline:

```text
load markdown
-> parse frontmatter/header metadata
-> build parent chunks
-> build semantic child chunks
-> link neighbors
-> write chunks.jsonl
-> validate models.lock.json
-> build BM25
-> generate embeddings
-> create/validate Qdrant collection
-> upsert vector points
-> write manifest
-> update ready state
```

当前 `/admin/reindex` 不能只是清缓存或标记刷新，必须实际执行上述 pipeline。若任何步骤失败，异步 job 状态必须为 `failed`，并保留错误原因。

`service/scripts/build_index.py` 是必须实现的离线索引入口。如果仓库中仍只有空 `service/scripts/__init__.py`，说明该能力尚未完成，不能按脚本方式部署或验收。

`models.lock.json` 写入和校验:

- 写入者是 `service.scripts.download_models` 或服务器模型下载步骤，不是 `/admin/reindex`。
- 文件必须在 reindex 之前存在，记录 embedding/reranker 的 `model_id`、`revision`、本地路径和关键文件 hash。
- `/admin/reindex` 和 `build_index.py` 只读取并校验 `models.lock.json`；校验失败时 job 失败，不自动重写。
- RAG Service 启动时也要校验当前模型路径和 lock 文件一致，否则对应 domain `/ready` 返回 `model_unavailable`。

reindex 按 domain 独立执行:

- `bank_stmt` 需要 embedding、Qdrant、reranker 和 BM25。
- `config_gen` 是 keyword-only/BM25 namespace，不依赖 embedding/reranker。
- embedding 模型不可用时，`bank_stmt` reindex 失败，但不能阻塞 `config_gen` reindex。
- 每个 domain 独立维护 `IndexManifest`、BM25 manifest 和 readiness。

空知识库行为:

- loader/chunker 过滤后如果 `chunk_count=0`，本次 reindex 必须失败，job 状态为 `failed`，错误码 `empty_knowledge`。
- `empty_knowledge` 不写新的顶层 `IndexManifest`，不自增 `IndexManifest.version`，不创建或切换 Qdrant alias，也不切换 BM25 current。
- 如果该 domain 已有旧 `ActiveIndexSnapshot`，旧索引继续服务，`/ready` 保持旧 snapshot 的状态；失败原因只体现在 reindex job 状态和日志中。
- 如果该 domain 没有旧索引，`/ready` 返回 `503 unindexed`，`/rag/search` 返回 503，错误码 `unindexed`。
- `/rag/search` 返回空 `results=[]` 只代表已有可用索引但没有命中，不能用来表示空知识库或索引未构建。
- 如果某个 domain 业务上允许知识库为空，应将该 domain `enabled=false`，而不是发布一个空索引。

在线搜索行为:

- reindex 期间 `/rag/search` 默认继续使用旧索引。
- 新索引构建到临时 BM25 目录和 Qdrant candidate collection。
- job 成功后通过发布事务统一切换 `IndexManifest.version`、Qdrant alias、BM25 current、chunks 内存索引和 BM25 reader。
- 发布事务持有 domain 级发布锁；新进入的 `/rag/search` 必须等待发布完成，超过 `reindex.publish_lock_timeout_seconds` 返回 `503 publishing`，不得在切换窗口内混用新旧组件。
- job 失败时旧索引继续服务，job 状态为 `failed`。
- 首次部署没有旧索引时，`/ready` 为 `503 unindexed`，`/rag/search` 返回 503。

Qdrant collection alias 命名:

- `domains.<domain>.collection` 是对外稳定 alias，例如 `bank_stmt.collection = bank_stmt_knowledge`。
- 搜索、debug API 和 `/ready` 都以 alias 作为逻辑 collection 名，不把物理 collection 名暴露给 payment 后端。
- 物理 collection 命名为 `<alias>__v<version:06d>__<job_id_short>`，例如 `bank_stmt_knowledge__v000012__a1b2c3d4`。
- 首次构建使用 `version=1`；后续 reindex 使用当前 manifest version + 1。
- 构建期间直接写入 candidate 物理 collection，不写入 alias 指向的旧 collection。
- candidate collection 只有在 embedding、payload index、point count、sample search 和 manifest 校验全部成功后，才允许进入发布事务。
- 发布成功后 alias 指向新的物理 collection；旧 collection 默认保留最近 `qdrant.keep_versions=2` 个版本用于回滚，超过数量的旧版本由后台清理任务或运维命令删除。
- staging/candidate 构建失败时必须删除本次 candidate collection；删除失败只记录告警，不影响旧索引继续服务。

BM25 + Qdrant 双组件发布事务:

1. 准备阶段不改 active 状态: 构建 BM25 staging、加载新 BM25 reader、加载新 chunks 内存索引、构建并校验 Qdrant candidate collection。
2. 记录旧状态: 当前 Qdrant alias target、BM25 current 目录、BM25 reader 引用、chunks 内存索引和 `IndexManifest.version`。
3. 获取 domain 级发布锁。多 worker 部署时该锁必须使用 Redis 或等价分布式锁；单进程也必须用本地写锁阻止并发 search 进入切换窗口。无法在 `reindex.publish_lock_timeout_seconds` 内进入稳定 generation 的新请求返回 `503 publishing`。
4. 在发布锁内再次检查旧 manifest version 未变化；若已变化，放弃本次发布并标记 `failed_conflict`。
5. 先执行 Qdrant alias 原子更新，将 alias 从旧物理 collection 指向新物理 collection。
6. Qdrant alias 成功后，切换 BM25 `.current` 到新目录，并替换 BM25 reader 与 chunks 内存索引。
7. 写入新的顶层 `IndexManifest`，更新 ready 状态，释放发布锁。
8. 如果 Qdrant alias 更新失败，本地状态未变，直接标记 job `failed`。
9. 如果 Qdrant alias 已成功但 BM25 切换或 manifest 写入失败，必须在发布锁内将 Qdrant alias 回滚到旧 collection，并恢复旧 BM25 reader/chunks/current。
10. 如果回滚也失败，domain 必须进入 `503 publish_inconsistent`，`/rag/search` 不得继续服务；需要人工修复 alias 或重新提交 reindex。

RetrievalService 并发模型:

- 每个 domain 持有一个不可变 `ActiveIndexSnapshot`，字段以 §6.5 的类型定义为准。
- `/rag/search` 请求开始时只读取一次当前 snapshot 引用，并在整个请求生命周期内使用该 snapshot；请求中途即使 reindex 发布成功，也不能切换到新 snapshot。
- snapshot 引用替换采用 RCU 模式: 发布事务构造并校验新 snapshot 后，用一次 Python 引用赋值替换 active snapshot。
- 搜索主路径不持有长时间读锁；只允许在读取 snapshot 引用或等待发布锁稳定 generation 时使用极短临界区。
- 已经开始的搜索请求继续持有旧 snapshot 引用，旧 BM25 reader 和 chunks dict 由 Python 引用计数或 GC 在无请求引用后自然释放，不需要主动销毁。
- Qdrant client 可以作为进程级共享对象，但 query 必须使用 snapshot 中记录的稳定 alias/generation；不能在一次请求里重新读取 domain active alias。
- sentence-transformers 的 `SentenceTransformer.encode()` 和 `CrossEncoder.predict()` 不直接在 FastAPI event loop 中执行，必须通过受限 executor 或专用 worker 调用。
- CPU 部署默认使用 `runtime.model_executor=threadpool`，并用 `embedding_max_concurrency`、`reranker_max_concurrency` 信号量限制并发，默认各为 1，避免 GIL/BLAS 线程争抢把服务拖死。
- GPU 部署也必须保留并发信号量；是否放大并发由压测决定，不能让每个请求无界并行进入同一个模型实例。
- reindex 后台 job 复用同一套模型 executor/信号量或使用独立 reindex worker；不允许与在线 search 争抢到导致 `/rag/search` 长时间饥饿。若资源不足，reindex 降速，search 优先。

多 worker manifest 热加载:

- 首选机制为 Redis pub/sub: 发布事务写入 manifest 后，向 `rag:index-updated:<domain>` 发布 `{domain, version, manifest_path, qdrant_alias}`。
- 每个 worker 订阅该 channel，收到更高 `version` 后加载 manifest、BM25 current、chunks.jsonl 和校验 Qdrant alias，再替换本进程 snapshot。
- Redis pub/sub 不是可靠队列，worker 可能错过消息；因此必须同时启用 manifest 文件轮询兜底。
- 轮询机制为定时检查 `indexes/manifests/<domain>.json` 的 `version` 和 `mtime`，默认 `runtime.manifest_poll_interval_seconds=2`。
- 不使用 inotify/watchdog 作为唯一机制；它可以作为优化，但不能替代跨平台轮询兜底。
- 发现远端 version 更高但本 worker 加载失败时，该 worker 对该 domain 返回 `503 reload_failed`，不得继续用旧 snapshot 长期服务。
- 单 worker 部署也使用同一套 snapshot reload 逻辑，避免本地和多 worker 代码路径分叉。

`runtime` 配置字段:

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `search_snapshot_mode` | `rcu` | 搜索请求持有不可变 `ActiveIndexSnapshot`；发布时用新 snapshot 引用替换旧引用。当前唯一合法值是 `rcu`。 |
| `model_executor` | `threadpool` | embedding/reranker 推理放入受限线程池，不能阻塞 FastAPI event loop。当前唯一合法值是 `threadpool`；后续如引入独立进程池需新增实现。 |
| `embedding_max_concurrency` | `1` | 同一进程内 embedding 推理并发上限。CPU 默认 1 是为了避免 GIL、tokenizer 和 BLAS 线程争抢导致尾延迟失控；是否放大必须压测。 |
| `reranker_max_concurrency` | `1` | 同一进程内 CrossEncoder rerank 并发上限。reranker 通常比 embedding 更重，默认 1；GPU/高核 CPU 放大前必须压测。 |
| `manifest_reload` | `redis_pubsub_with_polling_fallback` | 多 worker 热加载机制。Redis pub/sub 负责快速通知，manifest 文件轮询兜底防丢消息。 |
| `manifest_poll_interval_seconds` | `2` | manifest 轮询间隔；用于发现错过的 pub/sub 消息或 worker 重启后的版本变化。 |

这些字段是并发模型契约，不是性能调参的随意开关。修改它们必须同步更新压测基线和 `/ready`/`/rag/search` 超时策略。

BM25 组件在发布事务中的约束:

发布事务是唯一权威切换协议；BM25 不允许实现一套独立于 Qdrant alias 的 swap 流程。

1. 准备阶段在同一文件系统下构建到 `indexes/bm25/<domain>.staging/<job_id>/`。
2. 准备阶段写入 BM25 子 manifest，并校验 `chunks.jsonl`、`bm25.pkl`、`tokenizer.json` 文件 hash。
3. 准备阶段从 staging 目录加载新的 BM25 reader 和 chunks 内存索引，并执行 smoke search；失败则 job `failed`，不进入发布事务。
4. 发布事务步骤 6 才能切换 `indexes/bm25/<domain>.current`，并用新的 `ActiveIndexSnapshot` 一次性替换旧 snapshot。
5. BM25 组件不得单独写入顶层 `IndexManifest.version`，也不得在 Qdrant alias 成功发布前替换 active reader/chunks。
6. 若发布事务回滚，BM25 current、reader、chunks 必须回到事务开始前记录的旧 snapshot。

reindex job 状态恢复:

- Redis 保存 job 状态，但每个 running job 也必须写本地 job marker: `indexes/jobs/<job_id>.json`。
- 服务启动时扫描 running marker；若对应临时目录存在且未完成，标记为 `failed_interrupted`，不自动继续构建。
- `reindex.running_timeout_seconds` 控制运行超时，超过后标记 `failed_timeout`。
- `reindex.job_ttl_seconds` 只控制终态记录保留时间，不代表运行超时。
- 重新提交同一 domain reindex 必须幂等清理旧 staging 目录。

reindex 并发提交策略:

- 同一 domain 同一时间只允许一个 running job。
- 如果已有 running job，新提交默认返回 `409 job_already_running`，并返回当前 `job_id`。
- 不做自动取消，也不排队；取消能力以后如有需要单独实现 `POST /admin/reindex/{job_id}/cancel`。
- 不同 domain 可以并行 reindex，但各自独立 staging、manifest、ready 状态。

### 7.1 Header Metadata Chunk

Markdown 解析必须保留:

- 文件路径。
- 一级到六级标题。
- frontmatter。
- 表格/代码块边界。
- chunk 所属章节路径。

embedding_text 格式规范:

```text
标题路径: 一级标题 > 二级标题 > 三级标题
来源: 字段定义/amount.md
正文:
...
```

该格式是索引契约，不是实现建议。修改标题前缀、来源前缀、分隔符、同义词 tags 拼接方式或正文拼接顺序，都必须提升 `chunk_version` 并重建 Qdrant/BM25 索引。返回给 LLM 或 Agent 的 `content` 不拼接这些增强前缀，标题进入 `metadata` 和 `citation`。

### 7.2 Semantic Chunking

语义分块用于 child chunk。

token 计数器归属:

- `chunking.child_max_tokens`、`chunking.child_min_tokens`、`chunking.parent_max_tokens`、`chunking.overlap_tokens` 使用 embedding 模型 tokenizer 计数。
- embedding_text 拼接和长度保护使用 embedding 模型 tokenizer 计数。
- rerank 版 enriched context 使用 reranker 模型 tokenizer 计数，并受 `models.reranker.max_length` 限制。
- answer 版 enriched context 使用 LLM tokenizer 计数，并受 `context_enrich.max_context_tokens` 限制。
- `/rag/answer` 的 LLM prompt/context 预算使用 LLM tokenizer 计数，不复用 embedding/reranker tokenizer。
- 若目标 tokenizer 不可加载，相关 domain 的 reindex 或 ready 必须失败，不能用字符数估算代替 token 数。

`tokenizers.*` 的值是 tokenizer 来源别名，不是模型名。`embedding` 映射到 `models.embedding.model_id/path`，`reranker` 映射到 `models.reranker.model_id/path`，`llm` 映射到 `llm.model`。

推荐实现:

1. 先按 Markdown 标题、段落、列表、表格做结构切分。
2. 对长段落按句子切分。
3. 用 embedding 模型计算相邻句子相似度。
4. 相似度低于阈值或 token 超限时切块。
5. 保证每个 child chunk 不截断表格、代码块和列表项。

默认参数:

```yaml
models:
  embedding:
    model_id: BAAI/bge-small-zh-v1.5
    semantic_similarity_threshold: 0.62

chunking:
  # 当前唯一合法值为 semantic；fixed/recursive 需要新增实现后再开放。
  mode: semantic
  child_max_tokens: 350
  child_min_tokens: 80
  parent_max_tokens: 1200
  overlap_tokens: 40
```

如果 embedding 模型不可用，reindex 必须失败，不降级为硬切分，避免线上索引质量不可控。

这里的失败只针对需要 embedding 的 domain，例如 `bank_stmt`。`config_gen` 如果保持 keyword-only，不应因为 embedding 不可用而失败。

chunking 配置校验:

- `overlap_tokens` 必须满足 `0 <= overlap_tokens < child_min_tokens`。
- `child_min_tokens` 必须满足 `0 < child_min_tokens <= child_max_tokens`。
- `child_max_tokens` 必须满足 `child_max_tokens <= parent_max_tokens`。
- 违反上述约束时，使用语义分块的 domain `/ready` 返回 `503 invalid_config`；`/admin/reindex` 对该 domain 返回 failed，错误码 `invalid_config`。
- `keyword_simple` domain 不使用语义分块，不因全局 chunking 参数错误被标记不可用；但如果未来为该 domain 启用 semantic chunking，必须同样执行这些校验。

`semantic_similarity_threshold=0.62` 只对 `BAAI/bge-small-zh-v1.5` 当前 revision 校准有效。更换 embedding 模型、revision、normalize 策略或 embedding_text 格式时，必须重新校准该阈值，并提升 `chunk_version` 后重建索引。

### 7.3 Small-to-Big 父子块

父块用于回答上下文，子块用于召回。

规则:

- parent chunk 通常对应 Markdown 一个自然章节。
- child chunk 由 parent chunk 语义切分产生。
- Qdrant 存 child chunk 向量。
- BM25 默认索引 child chunk，同时保留 parent 映射。
- BM25 的 child 索引文本必须强制拼入 parent heading_path 和 parent title，避免关键词只出现在父级标题时漏召回。
- parent chunk 默认不作为独立 BM25 候选返回；如后续启用 parent BM25 候选，必须标记 `chunk_type=parent` 并在 fusion/rerank 中单独处理。
- 检索命中 child 后，可以按配置拉取 parent。

配置:

```yaml
small_to_big:
  enabled: true
  return_parent: true
  max_parent_tokens: 1200
```

### 7.4 Context Enrich 邻域增强

命中 child chunk 后补充前后邻居。

配置:

```yaml
context_enrich:
  enabled: true
  before: 1
  after: 1
  max_context_tokens: 1800
```

约束:

- context_enrich 是否执行由 `domains.<domain>.enable_context_enrich` 控制；全局 `context_enrich.enabled` 只表示组件可用和默认参数，不强制所有 domain 执行该步骤。
- context_enrich 的评分单位永远是命中的 child chunk，不把 parent 或 neighbor 变成新的独立候选。
- 执行顺序固定为: child 命中 -> 条件拉取 parent -> 拉取 child 的 prev/next neighbors -> 去重 -> 组装 enriched context。
- parent 拉取子步骤依赖 `small_to_big.enabled=true` 且命中 chunk 有 `parent_id`；如果 `small_to_big.enabled=false` 或 `parent_id=None`，静默跳过 parent 拉取，但 neighbor 增强仍然可用。
- `context_enrich.enabled=true` 不要求启动时拒绝 `small_to_big.enabled=false`，但这种组合只能提供邻域增强，不能提供父块补全。
- 邻居必须属于同一 `source` 和同一 parent。
- 只补命中 child 的邻居，不递归补 parent 的邻居，也不补 neighbor 的邻居。
- 不跨文档拼接。
- 去重 key 为 `chunk_id`；多个命中 child 指向同一 parent 时 parent 只保留一次。
- 去重后再进入 rerank 或 answer context。

## 8. 检索执行流程

### 8.1 `/rag/search`

`/rag/search` 不是固定单一路径，必须先读取 `domains.<domain>.pipeline_profile` 决定执行组件。`retrieval.mode` 表示召回模式，`pipeline_profile` 表示完整流水线形态；二者必须一致校验，例如 `keyword_simple` 不能配置为 `retrieval_mode=hybrid`。

请求 `mode` 兼容规则:

- 请求未传 `mode` 时，使用 `domains.<domain>.retrieval_mode`。
- 请求传了 `mode` 时，只能选择当前 `pipeline_profile` 支持的模式，不能通过 API 参数启用 domain 未配置的组件。
- 不允许静默降级或忽略 `mode`。不兼容时返回 HTTP 400，错误码 `mode_not_supported_for_profile`。
- 如果 domain 配置自身不兼容，例如 `pipeline_profile=keyword_simple` 但 `retrieval_mode=hybrid`，该 domain `/ready` 返回 `503 invalid_config`，search 请求返回同名 503。

兼容表:

| profile | 支持的请求 mode | 不支持时行为 |
| --- | --- | --- |
| `hybrid_advanced` | `hybrid`、`keyword`、`vector` | 返回 `400 mode_not_supported_for_profile` |
| `keyword_simple` | `keyword` | 返回 `400 mode_not_supported_for_profile` |

示例: `POST /rag/search {"domain":"config_gen","mode":"hybrid"}` 必须返回 `400 mode_not_supported_for_profile`，不能退化为 keyword，也不能返回空结果伪装成功。

分派流程:

```text
receive query
-> validate domain
-> load domain config
-> select pipeline_profile
-> execute profile pipeline
-> normalize response
```

当前允许的 profile:

| profile | 默认 domain | 执行步骤 | 跳过步骤 |
| --- | --- | --- | --- |
| `hybrid_advanced` | `bank_stmt` | keyword -> vector -> RRF -> context_enrich -> rerank -> citation | 无 |
| `keyword_simple` | `config_gen` | keyword -> score normalize -> citation | vector、RRF、context_enrich、rerank |

`hybrid_advanced` 执行路径:

```text
validate domain
-> keyword recall top_k_keyword
-> vector recall top_k_vector
-> RRF fusion
-> context_enrich, only when domains.<domain>.enable_context_enrich=true
-> CrossEncoder rerank, only when domains.<domain>.require_reranker=true
-> citation builder
-> normalize response
```

`keyword_simple` 执行路径:

```text
validate domain
-> keyword/BM25 recall
-> score normalize
-> citation builder
-> normalize response
```

默认参数:

```yaml
retrieval:
  top_k_keyword: 20
  top_k_vector: 40
  rrf_k: 60
  top_k_rerank: 5
```

当前 `rag_service.yaml` 中若仍使用旧字段，需要改名:

| 旧字段 | 新字段 | 默认值 |
| --- | --- | --- |
| `keyword_recall_top` | `top_k_keyword` | 20 |
| `vector_recall_top` | `top_k_vector` | 40 |
| `rerank_top_k` | `top_k_rerank` | 5 |

### 8.2 BM25/关键词召回

BM25 索引落盘:

```text
/data/payment-rag/indexes/bm25/<domain>/
  bm25.pkl
  chunks.jsonl
  tokenizer.json
  manifest.json
```

本地开发默认路径:

```text
backend/rag-service/.local/indexes/bm25/<domain>/
```

关键词召回必须使用统一同义词源:

```text
service/core/synonyms.py
```

禁止在 keyword、vector、BM25 模块内复制多份同义词表。

filters 行为:

- `/rag/search.filters` 必须同时作用于 keyword recall 和 vector recall。
- vector recall 使用 Qdrant payload filter。
- keyword/BM25 recall 在候选评分后做 metadata 后过滤，再按过滤后的结果截断到 `top_k_keyword`。
- 如果后过滤导致候选不足，不允许补入不满足 filters 的 chunk。
- RRF 只能融合已过滤的 keyword/vector 候选，避免未过滤 keyword 高分结果污染 hybrid 排序。

### 8.3 Vector Recall

Qdrant point payload 至少包含:

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

必须创建 Qdrant payload index 的字段:

- `source`
- `heading_path`
- `category`
- `knowledge_version`
- `embedding_model`
- `embedding_revision`
- `statement_type`

这些字段可能参与 `filters` 或运维排查；未建 payload index 时，Qdrant 可能退化为 payload 全扫描。

查询时必须校验:

- collection 存在。
- vector size 匹配。
- manifest 的 embedding revision 与当前模型一致。

当前 `QdrantVectorStore` 若仍是接口桩，必须在实现 `Vector Recall` 前补齐 upsert/search/delete/count 和 payload index 创建逻辑。

Qdrant payload 存储策略:

- Qdrant 只存 child chunk 向量和 child 级定位字段。
- `content` 字段为 child 原始正文，受 `chunking.child_max_tokens` 控制，不能存 parent 全文。
- parent chunk、neighbor 映射和完整上下文存放在 `chunks.jsonl` / manifest store 中，通过 `chunk_id`、`parent_id`、`prev_chunk_id`、`next_chunk_id` 回表读取。
- 如果未来 child content 也导致 payload 过大，可以改为 `content_preview` + `chunk_id` 回表；但当前契约保留 child `content`，方便检索调试和 admin 查询。
- Qdrant payload 不存 `embedding_text`，避免同义词 tags 和标题前缀造成存储膨胀。

`chunks.jsonl` 回表策略:

- RetrievalService 启动或 domain ready 后，必须把当前版本 `chunks.jsonl` 加载为内存索引 `dict[chunk_id, Chunk]`。
- context_enrich、citation builder 和 `/rag/answer` 回表只能查内存索引，禁止每次搜索扫描 JSONL 文件。
- reindex atomic swap 成功前，先从 staging `chunks.jsonl` 构建并校验新的内存索引；校验失败不切换 current。
- current swap 成功后，再用新的内存索引替换当前引用；旧索引对象允许由正在执行的请求自然释放。
- 多 worker 部署时，每个 worker 都必须按 §7 的 Redis pub/sub + manifest 轮询机制发现 `IndexManifest.version` 变化，并热加载自己的内存索引。

### 8.3.1 heading_path 规则

`heading_path` 用 Markdown 标题层级表示，不把完整文件路径作为第一个标题。

规则:

- 如果 Markdown 有一级标题，从一级标题开始。
- 如果文件没有一级标题，用相对文件路径去掉扩展名后的路径段作为前置上下文。
- 深层路径使用所有路径段，不只取直接父目录。
- 文件夹名可以作为 fallback 上下文，但不重复加入已有同名标题。
- API 展示字段 `heading` 取 `heading_path[-1]`；如果 `heading_path` 为空，取文件名 stem。

示例:

| 文件 | Markdown 标题 | heading_path | heading |
| --- | --- | --- | --- |
| `字段定义/amount.md` | `# 字段定义` -> `## 交易金额` | `["字段定义", "交易金额"]` | `交易金额` |
| `字段定义/amount.md` | `## 交易金额` | `["字段定义", "交易金额"]` | `交易金额` |
| `字段定义/微信/个人/amount.md` | `## 交易金额` | `["字段定义", "微信", "个人", "交易金额"]` | `交易金额` |
| `规则/微信.md` | 无标题 | `["规则", "微信"]` | `微信` |

### 8.3.2 content_hash 规范化

`content_hash = sha256(normalized_content)`，规范化规则必须固定:

1. 不包含 frontmatter。
2. 不包含自动拼接的 embedding 标题前缀或同义词 tags。
3. 换行统一为 `\n`。
4. 去除行尾空白。
5. 连续三个及以上空行压缩为两个空行。
6. 首尾 trim。
7. 保留中文标点、英文大小写、表格内容和代码块内容。

`chunk_id` 按 §6.1 的 JSON 序列化和固定分隔符规范生成。只改 metadata 不改正文时，`content_hash` 不变；改正文时，`content_hash` 和 `chunk_id` 都应变化。

规则 #5 保留两个空行，是为了保留 Markdown 中“段落间距”和“章节分隔”的弱结构信号；三个及以上空行通常只代表编辑冗余。实现不得自行改成压缩为一个空行，否则会导致 `content_hash` 和 `chunk_id` 漂移。

责任边界:

- loader 必须保证 `Chunk.content` 已经剥离 frontmatter。
- hasher 只接收 `Chunk.content`，不再尝试识别或切除 frontmatter。
- 若未来 loader 保留 frontmatter，只能放入 `metadata.frontmatter`，不能混入 `content`。

### 8.4 RRF 融合

RRF 输入:

```text
keyword_results
vector_results
```

单模式行为:

- `mode=keyword`: 只执行 keyword recall，跳过 vector recall 和 RRF fusion，`final_score = keyword_score`。
- `mode=vector`: 只执行 vector recall，跳过 keyword recall 和 RRF fusion，`final_score = vector_score`。
- `mode=hybrid`: 执行 keyword + vector + RRF。
- 单模式下 `rrf_score = null`，但仍可按配置执行 rerank。

公式:

```text
score = sum(1 / (rrf_k + rank_i))
```

输出要保留每一路 rank 和 score，便于 eval 和排查:

```json
{
  "keyword_rank": 3,
  "vector_rank": 1,
  "rrf_score": 0.0325
}
```

### 8.5 CrossEncoder Rerank

rerank 输入:

```text
(query, enriched_chunk_text)
```

要求:

- 采用方案 A: 以命中 child 为候选单位，将 child + parent 摘要/截断片段 + neighbors 组装成一个 `enriched_chunk_text` 后送入 reranker。
- 不采用方案 B；parent 和 neighbors 不作为独立候选分别打分。
- `enriched_chunk_text` 必须受 `context_enrich.max_context_tokens` 和 reranker `max_length` 双重限制，优先保留 child 原文，其次 parent 关键片段，最后 neighbors。
- parent 关键片段算法固定为: 保留 parent heading_path；按句子切分 parent content；优先选择包含 child content 的句子窗口，若无法定位则选择与 query embedding 相似度最高的前 N 个句子；最后按原文顺序拼接。
- reranker score 归属于命中的 child chunk，citation 可以引用 child、parent 和 neighbor 中实际进入回答上下文的证据片段。
- 如果 `enriched_chunk_text` 中超过 30% 的有效字符来自 parent 或 neighbor，citation 必须同时包含对应 parent/neighbor chunk_id，不能只引用 child。
- 有效字符定义为: 去除 Unicode 空白符和不可见控制字符后剩余的字符数；保留中文、英文、数字和可见标点。
- rerank 前候选数量不超过 `top_k_vector + top_k_keyword` 去重后的上限。
- rerank 后只返回 `top_k_rerank`。
- rerank 模型失败时，`bank_stmt` domain 的 `/ready` 不应为 ok；运行中失败要返回显式错误，不静默降级为空结果。
- 禁止用 `hybrid(rerank_failed)` 这类特殊 retriever 字符串伪装成功结果。
- 允许的降级只能通过显式配置开启，例如 `retrieval.allow_rerank_fallback=true`，且默认必须为 `false`。

enriched context 分两版:

- rerank 版: 用 reranker tokenizer 和 `models.reranker.max_length` 截断，只供 CrossEncoder 评分。
- answer 版: rerank 完成后，基于最终 citation/chunk_id 回表 `chunks.jsonl`，重新组装 parent + child + neighbors，上限使用 `context_enrich.max_context_tokens` 和 LLM tokenizer。

`/rag/answer` 不能直接复用 rerank 版截断文本作为 LLM 上下文；否则会把 LLM 可用上下文错误限制到 reranker 的 512 tokens。

### 8.6 Domain Pipeline Profile

检索流水线由 domain 配置决定，不把 `bank_stmt` 的高级链路隐式套到所有 domain。

`bank_stmt` 默认 profile:

```yaml
domains:
  bank_stmt:
    retrieval_mode: hybrid
    pipeline_profile: hybrid_advanced
    require_vector: true
    require_reranker: true
    enable_context_enrich: true
```

执行路径:

```text
validate domain
-> keyword recall
-> vector recall
-> RRF fusion
-> context_enrich(parent + neighbors)
-> CrossEncoder rerank
-> citation builder
-> normalize response
```

`config_gen` 默认 profile:

```yaml
domains:
  config_gen:
    retrieval_mode: keyword
    pipeline_profile: keyword_simple
    require_vector: false
    require_reranker: false
    enable_context_enrich: false
```

执行路径:

```text
validate domain
-> keyword/BM25 recall
-> score normalize
-> citation builder
-> normalize response
```

`config_gen` 行为约束:

- 不执行 vector recall，也不访问 Qdrant。
- 不执行 RRF fusion；`score_breakdown.rrf_score = null`。
- 不执行 context_enrich；chunk 没有 parent/child/neighbor 语义，`parent_id/prev_chunk_id/next_chunk_id` 可以为 `None`。
- 不执行 rerank，即使全局 `retrieval.allow_rerank_fallback` 或 reranker 模型可用也不启用；domain 的 `require_reranker=false` 和 `pipeline_profile=keyword_simple` 优先。
- citation builder 仍执行，使用 keyword 命中的 chunk 原文和 heading_path 生成证据。
- `/rag/answer` 如未来支持 `config_gen`，也必须使用 keyword_simple 的 retrieved chunks，不做父子块展开。

新增 domain 时必须显式选择 `pipeline_profile`。允许值:

| profile | 适用 | 执行组件 |
| --- | --- | --- |
| `hybrid_advanced` | 流水问答 | keyword、vector、RRF、context_enrich、rerank、citation |
| `keyword_simple` | 配置生成/纯 Markdown 关键词库 | keyword、score normalize、citation |

## 9. Citation Builder

Citation 是 RAG Service 的核心输出，不交给 payment 后端临时拼。

Citation 字段:

```python
class Citation(BaseModel):
    index: int
    source: str
    heading_path: list[str]
    chunk_id: str
    parent_id: str | None
    content_hash: str
    quote: str | None
```

规则:

- `index` 从 1 开始，按最终 rerank 顺序生成。
- 默认不合并 citation，保证 `citation.index`、`chunk_id` 和 score 来源稳定可复现。
- 若后续启用 citation 合并，必须生成 `chunk_ids: list[str]` 和 `quotes: list[str]`，不得用单个 `chunk_id` 代表多个证据块。
- `quote` 只保留短摘录，用于定位证据，不返回整段原文。
- `/rag/answer` 的 LLM 只能基于 citation 对应内容回答。

## 10. API 契约

### 10.0 Domain 校验

所有 `/rag/*` 请求先校验 domain:

- domain 不存在: HTTP 404，错误码 `domain_not_found`。
- domain 存在但 `enabled=false`: HTTP 400，错误码 `domain_disabled`。
- domain 存在且 enabled，但依赖未 ready: HTTP 503，错误码使用 `/ready` 同名状态，例如 `unindexed`、`model_unavailable`、`dependency_unavailable`。

`config_gen.enabled=false` 时，`POST /rag/search {"domain":"config_gen"}` 必须返回 `400 domain_disabled`，不能静默退回 `bank_stmt`，也不能返回空结果伪装成功。

### 10.0.1 安全控制

安全策略的部署细节以 [RAG服务部署与接入指南.md](RAG服务部署与接入指南.md) §17 为准；本文只定义 RAG Service API 的生产边界。

- `server.auth_mode="none"` 只允许本地开发和离线测试使用，生产必须改为 `header`，或由内网 mTLS/API 网关提供等价鉴权。
- `/admin/reindex` 是管理端点，生产必须限制在管理网络或管理员 token；普通 payment 后端业务请求不得持有该权限。
- `/rag/eval/search` 是 eval/debug 端点，只允许测试环境或内网调用；生产必须通过反向代理、API 网关或 FastAPI 中间件拦截，不得暴露公网。
- `eval.enable_debug_api=false` 时，`/rag/eval/search` 必须返回 HTTP 404，错误码 `debug_api_disabled`；只有显式开启后才允许进入后续 domain 校验和检索。
- `/rag/search` 和 `/rag/answer` 也必须经过服务级鉴权或内网访问控制；不能因为它们是业务接口就绕过 `auth_mode`。
- 鉴权失败统一返回 HTTP 401/403，不返回空结果。

### 10.1 `/rag/search`

最终契约: `/rag/search` 的每个 `results[]` 内包含 `citations`。这是调试、admin 检索和 Agent 证据归一化所需的证据元数据。

payment 后端迁移策略:

- `rag_search_tool.contextualize_result()` 默认不把 `citations` 原样透传到 `state["rag_results"]`。
- 当前 L4 仍只消费 `source/heading/content/score` 等稳定字段。
- 只有 ContextAssembler 明确支持 citation 展示结构后，tool 才能把远程 `citations` 转换成稳定 L4 引用格式。

请求:

```json
{
  "query": "交易金额字段是什么意思",
  "domain": "bank_stmt",
  "top_k": 5,
  "mode": "hybrid",
  "filters": {
    "statement_type": "wechat"
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

### 10.2 `/rag/eval/search`

仅限测试/内网。生产安全要求见 §10.0.1 和部署指南 §17。

请求:

```json
{
  "query": "交易金额字段是什么意思",
  "domain": "bank_stmt",
  "top_k": 5,
  "modes": ["keyword", "vector", "hybrid"],
  "include_raw": true
}
```

返回必须包含:

- keyword 原始结果。
- vector 原始结果。
- hybrid 融合重排结果。
- 每条结果的 source、chunk_id、score breakdown。

生产公网不得暴露该接口；默认 `eval.enable_debug_api=false` 时返回 `404 debug_api_disabled`。即使 `eval.enable_debug_api=true`，也必须由反向代理或中间件限制来源和凭证。

### 10.3 `/rag/answer`

`/rag/answer` 契约与 [RAG服务代码拆分指南.md](RAG服务代码拆分指南.md) 保持一致。它复用 `/rag/search` 的召回、fusion、rerank 排序和 citation builder，但在生成前必须基于最终 citation/chunk_id 回表重新组装 answer 版 enriched context，再调用 LLM。

请求:

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

`retrieved` 使用与 `/rag/search results[]` 相同的 `SearchResult` schema。`return_retrieved=false` 时返回空数组；`return_retrieved=true` 时返回本次用于生成答案的检索明细。`citations` 由 `with_citations` 控制，默认保留。

## 11. 配置模板

canonical 配置模板只维护一份，文件位置:

```text
backend/rag-service/config/rag_service.yaml
```

部署指南和本文都引用该文件。下面的 YAML 必须与 `backend/rag-service/config/rag_service.yaml` 保持字段级一致；如果实际配置新增字段，本节必须同步更新。

```yaml
server:
  host: "${RAG_SERVICE_HOST:0.0.0.0}"
  port: 8020
  api_key_env: "RAG_SERVICE_API_KEY"
  # none 仅限本地开发；生产必须使用 header 或由内网 mTLS/API 网关统一鉴权。
  auth_mode: "none"

paths:
  knowledge_root: "${RAG_KNOWLEDGE_ROOT:backend/knowledge_base}"
  index_dir: "${RAG_INDEX_DIR:backend/rag-service/.local/indexes}"
  bm25_index_dir: "${RAG_BM25_INDEX_DIR:backend/rag-service/.local/indexes/bm25}"
  model_lock_file: "${RAG_MODEL_LOCK_FILE:backend/rag-service/.local/indexes/manifests/models.lock.json}"

domains:
  bank_stmt:
    enabled: true
    dir: "流水问答"
    knowledge_dir: "${RAG_BANK_STMT_KNOWLEDGE_DIR:}"
    collection: "bank_stmt_knowledge"
    retrieval_mode: "hybrid"
    pipeline_profile: "hybrid_advanced"
    bm25_namespace: "bank_stmt"
    require_vector: true
    require_reranker: true
    enable_context_enrich: true
  config_gen:
    enabled: false
    experimental: true
    dir: "流水配置生成 RAG增强"
    knowledge_dir: "${RAG_CONFIG_GEN_KNOWLEDGE_DIR:}"
    retrieval_mode: "keyword"
    pipeline_profile: "keyword_simple"
    bm25_namespace: "parser_config_knowledge"
    require_vector: false
    require_reranker: false
    enable_context_enrich: false

models:
  embedding:
    path: "${RAG_EMBEDDING_MODEL:BAAI/bge-small-zh-v1.5}"
    model_id: "BAAI/bge-small-zh-v1.5"
    revision: "${RAG_EMBEDDING_REVISION:}"
    dim: 512
    device: "${RAG_EMBEDDING_DEVICE:cpu}"
    normalize: true
    semantic_similarity_threshold: 0.62
  reranker:
    path: "${RAG_RERANKER_MODEL:BAAI/bge-reranker-base}"
    model_id: "BAAI/bge-reranker-base"
    revision: "${RAG_RERANKER_REVISION:}"
    device: "${RAG_RERANKER_DEVICE:cpu}"
    max_length: 512

chunking:
  # 当前唯一合法值为 semantic；fixed/recursive 需要新增实现后再开放。
  mode: "semantic"
  child_max_tokens: 350
  child_min_tokens: 80
  parent_max_tokens: 1200
  overlap_tokens: 40

tokenizers:
  chunking: "embedding"
  embedding_text: "embedding"
  rerank_context: "reranker"
  answer_context: "llm"

small_to_big:
  enabled: true
  return_parent: true
  max_parent_tokens: 1200

context_enrich:
  enabled: true
  before: 1
  after: 1
  max_context_tokens: 1800

retrieval:
  mode: "hybrid"
  top_k_keyword: 20
  top_k_vector: 40
  rrf_k: 60
  rrf_top_n: 10
  top_k_rerank: 5
  default_top_k: 5
  dedup_jaccard_threshold: 0.8
  allow_rerank_fallback: false
  max_quote_chars: 160

reindex:
  async: true
  job_ttl_seconds: 86400
  running_timeout_seconds: 1800
  publish_lock_timeout_seconds: 5
  online_strategy: "atomic_swap"

runtime:
  search_snapshot_mode: "rcu"
  model_executor: "threadpool"
  embedding_max_concurrency: 1
  reranker_max_concurrency: 1
  manifest_reload: "redis_pubsub_with_polling_fallback"
  manifest_poll_interval_seconds: 2

eval:
  enable_debug_api: false

qdrant:
  url: "${QDRANT_URL:}"
  api_key_env: "QDRANT_API_KEY"
  prefer_grpc: false
  timeout: 10
  vector_size: 512
  distance: "Cosine"
  keep_versions: 2

redis:
  url: "${REDIS_URL:}"
  host: "${REDIS_HOST:localhost}"
  port: 6379
  db: 0
  password_env: "REDIS_PASSWORD"

llm:
  base_url: "${LLM_BASE_URL:}"
  api_key_env: "LLM_API_KEY"
  model: "${LLM_MODEL:}"
  timeout_seconds: 60

bm25:
  jieba_dict: ""
  stop_words: []
```

`tokenizers.*` 的值是来源别名，不是模型名；具体模型由 `models.embedding`、`models.reranker` 和 `llm` 配置段决定。

`max_quote_chars` 放在 `retrieval` 段，因为 quote 摘录是在检索结果上下文和 citation builder 阶段产生的，不单独维护 `citations` 顶层配置段。quote 使用字符数而不是 token，是因为 quote 面向 UI/日志定位和证据短摘录，不参与模型上下文预算；模型上下文预算继续使用 token 参数控制。

## 12. 开发顺序

1. 定义 `Chunk`、`SearchResult`、`Citation`、`IndexManifest` schema。
2. 实现 Markdown header metadata parser。
3. 实现 `content_hash` 和稳定 `chunk_id` 生成。
4. 实现 parent chunk builder。
5. 实现 semantic child chunker。
6. 实现 neighbor linker。
7. 实现 manifest writer、manifest store 和 manifest 校验。
8. 改造 BM25 index，索引 child chunk 并持久化 parent/neighbor 映射。
9. 改造 Qdrant upsert payload，写入 child chunk 向量和完整 metadata。
10. 实现 keyword/vector 两路召回的统一结果模型，并同步改造其调用方使用 Pydantic model。
11. 实现 RRF fusion，保留 score breakdown，并同步改造其调用方。
12. 实现 context enrich，并同步改造其调用方。
13. 修改 CrossEncoder rerank 失败处理，禁止静默降级，并同步改造其调用方。
14. 实现 citation builder。
15. 完成检索链路调用方收敛，确保 `keyword.py`、`vector.py`、`fusion.py`、`rerank.py`、`service.py` 不再传裸 `dict`。
16. 改造 `/admin/reindex` 为完整 index pipeline，并同步实现 `service/scripts/build_index.py` 复用同一 pipeline。
17. 改造 `/rag/search` 输出。
18. 改造 `/rag/answer`，只使用 citation 对应上下文。
19. 实现 `/rag/eval/search` 内网调试接口。
20. 补 eval dataset、baseline 和回归脚本。

## 13. 测试要求

单元测试:

- schema 序列化和向后兼容展示字段。
- `content_hash` 和稳定 `chunk_id` 生成。
- Markdown 标题解析。
- 表格和代码块不被语义切分截断。
- parent/child 映射稳定。
- neighbor 链接不跨文档。
- BM25 index cold start 可从磁盘恢复。
- Qdrant payload 包含 manifest 需要的字段。
- RRF 排序可复现。
- rerank score 写入 `score_breakdown`。
- reranker 失败时抛出显式错误，不返回 `hybrid(rerank_failed)`。
- citation index 稳定；排序 tie-break 固定为 `rerank_score desc -> rrf_score desc -> vector_score desc -> keyword_score desc -> chunk_id asc`。

集成测试:

- `/admin/reindex` 成功后 `/ready` 返回 ok。
- `/rag/search` 返回 hybrid 结果和 citation。
- `/rag/eval/search` 返回 keyword/vector/hybrid 三路结果。
- reranker 不可用时显式报错。
- manifest embedding revision 不一致时拒绝使用旧 collection。

回归评测:

- `keyword_recall_3`
- `vector_recall_3`
- `hybrid_recall_3`
- `source_coverage`
- `answer_rate`
- `refusal_accuracy`

性能目标:

- `/rag/search` warm p95 小于 500ms，按单个 domain 统计，不含 LLM answer，且要求 chunks/BM25 reader 已在内存、Qdrant 连接已建立、reranker 已预热。
- BM25 cold load 小于 3s，按单个 domain 统计；多个 domain 启动时分别计量，不把所有 domain 串行加载时间相加后对比该指标。
- Qdrant collection point count 与 manifest chunk count 一致。
- reindex job 失败时状态为 `failed`，不静默成功。

## 14. 验收标准

完成后应满足:

- RAG Service 能独立构建 Markdown 知识库索引。
- `service/schemas/rag.py` 包含 `Chunk`、`SearchResult`、`Citation`、`IndexManifest`，字段与本文一致。
- `knowledge/loader.py` 不再使用 `rel_path#i` 作为新索引主键。
- `/admin/reindex` 不只是清缓存，而是完整构建 BM25、Qdrant points 和 manifest。
- 检索链路包含 BM25、Vector、RRF、Context Enrich、Rerank。
- 返回结果包含稳定 citation 和 score breakdown。
- `/rag/answer` 只基于可追溯 citation 上下文生成。
- `/rag/eval/search` 可用于复现 keyword/vector/hybrid 三路召回指标。
- payment 后端不需要知道父子块、邻域、Qdrant payload、BM25 manifest 的内部细节。
- payment Agent 继续负责工具路由、任务拆解、证据不足处理和最终业务表达。
