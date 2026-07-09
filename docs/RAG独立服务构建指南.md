# RAG 独立服务构建指南

创建日期: 2026-07-08

## 1. 文档拆分

RAG 独立服务建设拆成两份详细文档:

- [RAG服务代码拆分指南.md](RAG服务代码拆分指南.md)
- [RAG服务部署与接入指南.md](RAG服务部署与接入指南.md)
- [RAG高级检索能力开发指南.md](RAG高级检索能力开发指南.md)

## 2. 总目标

将当前 payment-agent-ai 内置 RAG 从 `backend/app` 后端进程中完整拆出，建设为与 `backend/app` 平级的 `backend/rag-service`。最终效果:

- `backend/rag-service` 可独立启动。
- `backend/rag-service` 可独立部署到服务器。
- RAG Service 连接服务器上的 Qdrant、Redis、模型目录和知识库目录。
- payment 后端通过 `RAGClient` 调用 `/rag/search` 和 `/rag/answer`。
- payment 后端不再加载 embedding/reranker，也不再构建本地向量索引。

## 3. 总架构

```text
payment-agent-ai backend
  -> RAGClient
  -> HTTP /rag/search 或 /rag/answer
  -> RAG Service
      -> Markdown Knowledge Base
      -> Document Loader
      -> Chunker
      -> BM25/keyword index
      -> Embedding model
      -> Qdrant vector database
      -> RRF fusion
      -> CrossEncoder reranker
      -> Citation builder
      -> LLM answer generator
```

## 4. 职责边界

payment 后端:

- 会话、SSE、Planner、AgentLoop、工具治理、权限、解析、验真。
- 调用远程 RAG Service。
- 将 RAG 结果作为 `rag_results` 放入 S1 L4 参考层。

RAG Service:

- 知识库加载和分块。
- embedding、向量库、关键词索引。
- 混合检索、RRF、rerank。
- 引用构造和知识库问答。
- `/rag/search`、`/rag/answer`、`/admin/reindex`、`/health`、`/ready`。

## 5. 技术栈

| 层 | 推荐 | 说明 | 替代 |
| --- | --- | --- | --- |
| Web 服务 | FastAPI + Uvicorn | 与当前后端技术栈一致 | Flask, Litestar |
| 向量库 | Qdrant | 部署简单, collection 维度明确, Python SDK 成熟 | pgvector, Milvus, Weaviate |
| Embedding | BAAI/bge-small-zh-v1.5 | 当前配置使用, 512 维, 中文友好 | bge-base-zh-v1.5, bge-m3 |
| Reranker | BAAI/bge-reranker-base | 当前配置使用 | bge-reranker-large, Jina reranker |
| 关键词检索 | BM25 | 替代当前自定义关键词打分 | OpenSearch, Elasticsearch |
| 融合 | RRF | 稳定简单 | 加权融合, learning-to-rank |
| 缓存 | Redis | 缓存 query embedding、检索结果、answer | 本地 LRU |

## 6. 阅读顺序

1. 如果只做本地代码拆分，先读且只需要读 [RAG服务代码拆分指南.md](RAG服务代码拆分指南.md)。该文档明确本地代码服务端做到哪一步停止。
2. 如果要启动真实 RAG Service、下载模型、部署 Qdrant/Redis、构建索引、接入服务器，再读 [RAG服务部署与接入指南.md](RAG服务部署与接入指南.md)。
3. 如果要在迁移后的 RAG Service 上补齐高级检索能力，再读 [RAG高级检索能力开发指南.md](RAG高级检索能力开发指南.md)。

边界:

- 代码拆分指南管 `backend/app/rag -> backend/rag-service` 的代码迁移、API 契约、Tool 改造和本地 mock 测试。
- 部署与接入指南管服务器上的模型、向量库、Redis、Docker、知识库同步、生产索引和线上验收。
- 高级检索能力开发指南管 Semantic Chunking、父子块、邻域增强、Hybrid/RRF/Rerank、Citation、Manifest 和 Eval API 的开发细节。

## 7. 官方参考

- Qdrant quickstart: https://qdrant.tech/documentation/quickstart/
- Qdrant installation: https://qdrant.tech/documentation/installation/
- Sentence Transformers installation: https://sbert.net/docs/installation.html
- Hugging Face download guide: https://huggingface.co/docs/huggingface_hub/en/guides/download
- Hugging Face CLI guide: https://huggingface.co/docs/huggingface_hub/en/guides/cli
- FastAPI manual deployment: https://fastapi.tiangolo.com/deployment/manually/
