# RAG Service Agent 对接文档

## 服务概述

RAG Service 是一个独立的检索增强生成服务，提供银行流水知识库的检索和问答能力。

### 服务信息

| 项目 | 值 |
|------|-----|
| 服务地址 | `http://<host>:8020` |
| 版本 | 0.1.0 |
| 知识库 | 银行流水验真、交易错误、字段定义、指标说明等 |

### 健康检查

```bash
GET /health
```

**响应示例：**
```json
{
  "status": "ok",
  "service": "rag-service",
  "version": "0.1.0"
}
```

---

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/rag/search` | POST | 知识库检索 |
| `/rag/answer` | POST | 检索+LLM问答 |
| `/admin/reindex` | POST | 重建索引 |

---

## 1. 知识库检索 `/rag/search`

### 请求参数

```json
{
  "query": "string, 必填, 检索问题",
  "top_k": 5,
  "mode": "hybrid",
  "domain": "bank_stmt"
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | string | 是 | - | 检索问题文本 |
| top_k | int | 否 | 5 | 返回结果数量 |
| mode | string | 否 | hybrid | 检索模式: hybrid/vector/keyword |
| domain | string | 否 | bank_stmt | 业务域 |

### 响应结构

```json
{
  "results": [
    {
      "chunk_id": "sha256:xxx",
      "source": "验真/overview.md",
      "heading": "四种验真方式",
      "heading_path": ["四种验真方式"],
      "content": "## 四种验真方式\n\n...",
      "score": 0.9955,
      "score_breakdown": {
        "rerank_score": 0.9955,
        "keyword_score": 1.9286,
        "vector_score": 0.5643,
        "rrf_score": 0.015385,
        "final_score": 0.9955
      },
      "retriever": "hybrid",
      "citations": [
        {
          "index": 1,
          "source": "验真/overview.md",
          "heading": "四种验真方式",
          "quote": "..."
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
  "latency_ms": 3602.0
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| results | array | 检索结果列表 |
| results[].source | string | 知识库来源文件 |
| results[].heading | string | 文档标题 |
| results[].content | string | 文档内容片段 |
| results[].score | float | 最终相关性得分 (0-1) |
| results[].citations | array | 引用信息 |
| retriever | object | 检索器状态 |
| latency_ms | float | 响应延迟(毫秒) |

### 请求示例

**Python:**
```python
import httpx

response = httpx.post(
    "http://localhost:8020/rag/search",
    json={
        "query": "流水验真有几种方式",
        "top_k": 5
    }
)
result = response.json()
for item in result["results"]:
    print(f"[{item['score']:.4f}] {item['source']} - {item['heading']}")
    print(f"  {item['content'][:100]}...")
```

**cURL:**
```bash
curl -X POST http://localhost:8020/rag/search \
  -H "Content-Type: application/json" \
  -d '{"query": "流水验真有几种方式", "top_k": 5}'
```

---

## 2. 检索问答 `/rag/answer`

### 请求参数

```json
{
  "question": "string, 必填, 问题",
  "top_k": 5,
  "with_citations": true,
  "return_retrieved": false
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| question | string | 是 | - | 问题文本 |
| top_k | int | 否 | 5 | 检索数量 |
| with_citations | bool | 否 | true | 是否返回引用 |
| return_retrieved | bool | 否 | false | 是否返回原始检索结果 |

### 响应结构

```json
{
  "answer": "根据知识库信息，流水验真有四种方式：\n1. 结息验真...\n2. 余额连续性验真...\n3. 交易日期验真...\n4. 电子签章...",
  "citations": [
    {
      "index": 1,
      "source": "验真/overview.md",
      "heading": "四种验真方式",
      "quote": "..."
    }
  ],
  "retrieved": [],
  "retriever": {
    "mode": "hybrid",
    "keyword_available": true,
    "vector_available": true,
    "rerank_available": true
  },
  "latency_ms": 5359.4
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | LLM 生成的回答 |
| citations | array | 引用来源列表 |
| retrieved | array | 原始检索结果(可选) |
| latency_ms | float | 响应延迟(毫秒) |

### 请求示例

**Python:**
```python
import httpx

response = httpx.post(
    "http://localhost:8020/rag/answer",
    json={
        "question": "如何判断银行流水是否被篡改",
        "top_k": 5
    },
    timeout=60.0  # LLM 生成可能较慢
)
result = response.json()
print("回答:", result["answer"])
print("\n引用来源:")
for cite in result["citations"]:
    print(f"  [{cite['index']}] {cite['source']} - {cite['heading']}")
```

---

## 3. 重建索引 `/admin/reindex`

### 请求参数

```json
{
  "collection": "string, 必填, 知识库名称",
  "domain": "bank_stmt",
  "force": false
}
```

### 响应结构

```json
{
  "job_id": "rag-reindex-xxx",
  "status": "completed",
  "status_url": "/admin/reindex/rag-reindex-xxx"
}
```

---

## 错误响应

### 错误格式

```json
{
  "detail": "错误描述信息"
}
```

### 常见错误码

| HTTP 状态码 | 说明 |
|------------|------|
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 422 | 参数验证失败 |
| 500 | 服务器内部错误 |
| 504 | LLM 请求超时 |

---

## Agent 接入示例

### 完整 Python Agent 示例

```python
import httpx
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class RAGResult:
    source: str
    heading: str
    content: str
    score: float

@dataclass
class RAGAnswer:
    answer: str
    citations: List[dict]
    latency_ms: float

class RAGClient:
    """RAG Service 客户端"""

    def __init__(self, base_url: str = "http://localhost:8020"):
        self.base_url = base_url
        self.client = httpx.Client(timeout=60.0)

    def health_check(self) -> bool:
        """检查服务健康状态"""
        try:
            resp = self.client.get(f"{self.base_url}/health")
            return resp.status_code == 200
        except:
            return False

    def search(self, query: str, top_k: int = 5) -> List[RAGResult]:
        """知识库检索"""
        resp = self.client.post(
            f"{self.base_url}/rag/search",
            json={"query": query, "top_k": top_k}
        )
        resp.raise_for_status()
        data = resp.json()

        return [
            RAGResult(
                source=r["source"],
                heading=r["heading"],
                content=r["content"],
                score=r["score"]
            )
            for r in data["results"]
        ]

    def answer(self, question: str, top_k: int = 5) -> RAGAnswer:
        """检索问答"""
        resp = self.client.post(
            f"{self.base_url}/rag/answer",
            json={"question": question, "top_k": top_k}
        )
        resp.raise_for_status()
        data = resp.json()

        return RAGAnswer(
            answer=data["answer"],
            citations=data["citations"],
            latency_ms=data["latency_ms"]
        )


# 使用示例
if __name__ == "__main__":
    rag = RAGClient("http://localhost:8020")

    # 健康检查
    if not rag.health_check():
        print("RAG Service 不可用")
        exit(1)

    # 检索示例
    print("=== 检索测试 ===")
    results = rag.search("流水验真方式", top_k=3)
    for r in results:
        print(f"[{r.score:.4f}] {r.source}: {r.heading}")

    # 问答示例
    print("\n=== 问答测试 ===")
    answer = rag.answer("如何判断流水是否被篡改")
    print(f"回答: {answer.answer[:200]}...")
    print(f"延迟: {answer.latency_ms:.1f}ms")
```

### LangChain Tool 封装

```python
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import httpx

class RAGSearchInput(BaseModel):
    query: str = Field(description="检索问题")

class RAGSearchTool(BaseTool):
    name = "rag_search"
    description = "银行流水知识库检索工具。用于查询流水验真、交易错误、字段定义等知识。"
    args_schema = RAGSearchInput

    base_url: str = "http://localhost:8020"

    def _run(self, query: str) -> str:
        resp = httpx.post(
            f"{self.base_url}/rag/search",
            json={"query": query, "top_k": 5},
            timeout=30.0
        )
        resp.raise_for_status()
        data = resp.json()

        # 格式化输出
        output = []
        for r in data["results"][:3]:
            output.append(f"【{r['source']}】{r['heading']}\n{r['content'][:300]}")

        return "\n\n---\n\n".join(output)

# 使用
tool = RAGSearchTool()
result = tool.run("余额验真的计算方法")
print(result)
```

---

## 性能指标

| 操作 | 平均延迟 | 说明 |
|------|---------|------|
| `/health` | < 10ms | 健康检查 |
| `/rag/search` | 200-500ms | 检索(含Embedding+Qdrant+BM25+Rerank) |
| `/rag/answer` | 3-6s | 问答(含检索+LLM生成) |

---

## 注意事项

1. **超时设置**: `/rag/answer` 接口需要设置较长超时(建议 60s+)
2. **检索模式**: 默认 `hybrid` 模式效果最佳，可选择 `keyword` 或 `vector`
3. **知识库更新**: 新增知识文件后需调用 `/admin/reindex` 重建索引
4. **引用追溯**: 通过 `citations` 字段可追溯回答来源，增强可信度

---

## 联系方式

- 服务仓库: `/opt/uboss/haojie.liu/due-rag`
- 配置文件: `config/rag_service.yaml`
- Docker 编排: `docker/docker-compose.yml`
