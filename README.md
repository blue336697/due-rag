# RAG Service — Payment Agent AI 知识库检索服务

独立 RAG 服务，负责知识库加载、分块、索引、检索、重排、引用构造和知识库问答。

## 启动

```bash
cd backend/rag-service
pip install -r requirements.txt
PYTHONPATH=. uvicorn service.main:app --host 0.0.0.0 --port 8020 --reload
```

## API

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /ready` | 就绪检查（各 domain 独立状态） |
| `POST /rag/search` | 知识库检索 |
| `POST /rag/answer` | 知识库问答 |
| `POST /rag/eval/search` | Eval 多模式检索（内部/测试） |
| `POST /admin/reindex` | 提交异步重建任务 |
| `GET /admin/reindex/{job_id}` | 查询重建任务状态 |

## 迁移说明

从 `backend/app/rag/` 迁移而来。

- 移除 LangChain 依赖面
- InMemoryVectorStore → 自研实现
- MarkdownHeaderTextSplitter → 自研分块
- 保留 sentence-transformers 直接调用
