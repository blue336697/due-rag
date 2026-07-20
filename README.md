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
| `POST /admin/documents` | 上传并异步解析知识文档 |
| `GET /admin/ingestions/{job_id}` | 查询文档解析状态 |
| `GET /admin/documents/{id}/preview` | 预览规范 Markdown 和质量报告 |
| `POST /admin/documents/{id}/publish` | 发布文档并触发 Reindex |
| `DELETE /admin/documents/{id}` | 下线文档并触发 Reindex |

部署验收参见：

- [服务器部署与服务端验收测试计划](docs/test-plans/服务器部署与服务端验收测试计划.md)
- [本地调用服务器 RAG 服务测试计划](docs/test-plans/本地调用服务器RAG服务测试计划.md)

## 更新知识库

1. 将 UTF-8 Markdown 文件放入 `knowledge_base/流水问答/`。
2. 调用 `POST /admin/reindex`；接口返回 `202` 和 `job_id`。
3. 轮询 `GET /admin/reindex/{job_id}`，直到状态为 `completed`、`skipped` 或 `failed`。

```bash
curl -X POST http://localhost:8020/admin/reindex \
  -H "Content-Type: application/json" \
  -d '{"domain":"bank_stmt","collection":"bank_stmt_knowledge","force":false}'
```

重建采用版本化 Qdrant collection 和版本化 BM25 目录。新 generation 完整验证并写入 Manifest 后才切换运行时快照；失败时旧索引继续服务。

### 上传非标准文档

支持 `.md`、`.txt`、`.html`、`.docx`、`.pdf`、`.png`、`.jpg`。原文件保存在 ingestion 目录，解析结果同时保存为统一 Element JSON 和规范 Markdown。PDF 和图片调用公司 `br-ocr-v1` 服务：向 `/api/v1/file_parse` 上传 base64 文件，使用服务返回的 `task_uuid`（未返回时兼容请求 `task_id`）轮询 `/api/v1/result/{task_uuid}`，再将结构化 `page_parse_json` 转成带页码、标题层级和表格结构的标准文档。OCR 调用失败会使摄取任务明确失败，内容过短等质量问题会进入人工复核状态。

默认 OCR 地址为 `http://192.168.160.88:7557`，模型为 `br-ocr-v1`；部署时可通过 `RAG_OCR_BASE_URL` 和 `RAG_OCR_MODEL` 覆盖。

```bash
curl -X POST http://localhost:8020/admin/documents \
  -H "X-API-Key: $RAG_SERVICE_API_KEY" \
  -F "file=@规则说明.docx" \
  -F "domain=bank_stmt" \
  -F 'metadata={"category":"交易错误","statement_type":"personal"}'
```

状态为 `ready` 后先访问 preview；确认无误再发布：

```bash
curl -X POST "http://localhost:8020/admin/documents/<document_id>/publish" \
  -H "X-API-Key: $RAG_SERVICE_API_KEY"
```

人工维护的文档与接口发布的文档使用两个物理目录：

- `RAG_KNOWLEDGE_ROOT`：人工知识库，只读挂载。
- `RAG_MANAGED_KNOWLEDGE_ROOT/<domain>`：系统发布区，可写挂载；索引中的来源统一带 `_managed/` 前缀。

Reindex 会合并扫描两处内容并计算同一个知识版本。Docker 部署前需创建可写目录：

```bash
mkdir -p /opt/uboss/haojie.liu/rag-data/managed_knowledge
```

## 迁移说明

从 `backend/app/rag/` 迁移而来。

- 移除 LangChain 依赖面
- InMemoryVectorStore → 自研实现
- MarkdownHeaderTextSplitter → 自研分块
- 保留 sentence-transformers 直接调用
