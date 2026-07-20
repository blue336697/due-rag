# 本地调用服务器 RAG 服务测试计划

## 1. 目的

本计划在开发人员本地电脑执行，用于验证：

- 本地到服务器的网络和端口可达；
- API Key 鉴权符合预期；
- Search、Answer、上传、预览、发布、Reindex 和删除接口契约稳定；
- 从本地上传 Markdown 和扫描 PDF 可以完成远程摄取；
- 跨机器调用的延迟、并发成功率和错误响应可接受；
- 浏览器直连场景是否需要 CORS 或同源网关。

服务器侧必须先完成《服务器部署与服务端验收测试计划》，且 `/ready` 已返回 `200`。

## 2. 执行约束

1. 使用独立测试文档和唯一测试标识，不使用敏感业务材料。
2. 不在终端截图、日志或报告中展示完整 API Key。
3. 所有本地上传的测试文档必须在测试结束后调用删除接口清理。
4. 如果服务器不是可信内网环境，不允许直接通过 HTTP 传输 API Key 和文档，必须先配置 HTTPS 网关。

## 3. 本地变量

PowerShell：

```powershell
$RagServer = "<服务器IP或域名>"
$RagBase = "http://${RagServer}:8020"
$RagApiKey = "<从安全渠道取得的API Key>"
$Headers = @{ "X-API-Key" = $RagApiKey }
$TestRunId = Get-Date -Format "yyyyMMddHHmmss"
$TestToken = "RAG_LOCAL_E2E_$TestRunId"
$Domain = "bank_stmt"
```

## 4. 通过标准

- TCP 8020 可达；
- `/health` 和 `/ready` 返回 `200`；
- 未授权请求返回 `401`，正确 Key 可以访问受保护接口；
- keyword、vector、hybrid 三种检索均返回合法响应；
- Answer 返回答案、引用和可追溯 chunk；
- 从本地上传的 Markdown 和 PDF 可以完成远程摄取；
- 发布后能搜索，删除后不能再搜索；
- 连续 20 次搜索和 5 路并发搜索无 5xx，成功率 100%；
- 内网搜索 P95 建议不超过 5 秒，Answer 不超过配置的 60 秒；
- 所有错误场景返回明确的 4xx，而不是 500 或连接中断。

## 5. 测试用例

### L01：网络和公开端点

```powershell
Test-NetConnection $RagServer -Port 8020
Invoke-RestMethod "$RagBase/health"
Invoke-WebRequest "$RagBase/ready"
Invoke-WebRequest "$RagBase/openapi.json"
```

预期：

- `TcpTestSucceeded=True`；
- `/health` 返回 `status=ok`；
- `/ready` 返回 `200`、`status=ok`；
- OpenAPI 可读取。

记录本地到服务器的网络路径：

```powershell
tracert $RagServer
```

### L02：鉴权

无 Key：

```powershell
$Body = @{ query = "鉴权测试"; domain = $Domain; top_k = 1; mode = "hybrid" } | ConvertTo-Json
try {
    Invoke-WebRequest "$RagBase/rag/search" -Method Post -ContentType "application/json" -Body $Body
} catch {
    $_.Exception.Response.StatusCode.value__
}
```

错误 Key：

```powershell
try {
    Invoke-WebRequest "$RagBase/rag/search" -Method Post `
      -Headers @{ "X-API-Key" = "wrong-key" } `
      -ContentType "application/json" -Body $Body
} catch {
    $_.Exception.Response.StatusCode.value__
}
```

正确 Key：

```powershell
Invoke-RestMethod "$RagBase/rag/search" -Method Post `
  -Headers $Headers -ContentType "application/json" -Body $Body
```

预期依次为 `401`、`401`、`200`。

### L03：Search 接口契约

对同一个业务查询分别执行：

```powershell
$Modes = @("keyword", "vector", "hybrid")
$SearchResponses = @{}
foreach ($Mode in $Modes) {
    $Request = @{
        query = "银行流水交易金额规则"
        domain = $Domain
        top_k = 5
        mode = $Mode
        filters = @{}
    } | ConvertTo-Json -Depth 5
    $SearchResponses[$Mode] = Invoke-RestMethod "$RagBase/rag/search" `
      -Method Post -Headers $Headers -ContentType "application/json" -Body $Request
}
```

逐个检查：

- `results` 是数组；
- 返回数量不超过 `top_k`；
- 每条结果包含 `chunk_id`、`source`、`content`、`score`；
- `heading_path` 是数组；
- `retriever.mode` 与请求模式一致；
- `latency_ms` 为非负数字；
- citation 中包含 `source`、`chunk_id`、`content_hash`；
- API 响应不得包含仅供向量化使用的 `embedding_text`。

### L04：Answer 接口契约

```powershell
$AnswerBody = @{
    question = "银行流水中的交易金额应如何理解？"
    domain = $Domain
    top_k = 5
    with_citations = $true
    return_retrieved = $true
} | ConvertTo-Json

$Answer = Invoke-RestMethod "$RagBase/rag/answer" -Method Post `
  -Headers $Headers -ContentType "application/json" -Body $AnswerBody
$Answer | ConvertTo-Json -Depth 10
```

预期：

- `answer` 非空；
- `citations` 非空；
- citation 可以对应到 `retrieved` 中的 chunk；
- `retriever` 包含 keyword/vector/rerank 可用状态；
- 调用时间不超过 60 秒；
- 回答没有捏造检索结果中不存在的唯一事实。

### L05：从本地上传 Markdown

创建测试文件：

```powershell
$MarkdownPath = Join-Path $env:TEMP "rag-local-$TestRunId.md"
@"
# 本地远程调用验收材料

## 唯一规则

验证码 $TestToken 表示本地到服务器的上传、发布和检索链路正常。
"@ | Set-Content -LiteralPath $MarkdownPath -Encoding utf8
```

上传：

```powershell
$UploadRaw = & curl.exe -sS -X POST "$RagBase/admin/documents" `
  -H "X-API-Key: $RagApiKey" `
  -F "file=@$MarkdownPath" `
  -F "domain=$Domain" `
  -F 'metadata={"source":"local-remote-e2e"}'
$Upload = $UploadRaw | ConvertFrom-Json
$DocumentId = $Upload.document_id
$IngestJobId = $Upload.job_id
```

轮询摄取任务：

```powershell
do {
    Start-Sleep -Seconds 2
    $Ingest = Invoke-RestMethod "$RagBase/admin/ingestions/$IngestJobId" -Headers $Headers
    $Ingest | ConvertTo-Json -Depth 5
} while ($Ingest.status -notin @("ready", "awaiting_review", "failed"))
```

预期：Markdown 最终为 `ready`，不得为 `failed`。

预览：

```powershell
$Preview = Invoke-RestMethod "$RagBase/admin/documents/$DocumentId/preview" -Headers $Headers
$Preview | ConvertTo-Json -Depth 10
```

预期：`normalized_markdown` 包含 `$TestToken`，`quality.passed=true`。

### L06：远程发布、Reindex 和搜索

```powershell
$Published = Invoke-RestMethod "$RagBase/admin/documents/$DocumentId/publish" `
  -Method Post -Headers $Headers
$ReindexJobId = $Published.reindex_job_id
```

轮询：

```powershell
do {
    Start-Sleep -Seconds 2
    $Reindex = Invoke-RestMethod "$RagBase/admin/reindex/$ReindexJobId" -Headers $Headers
    $Reindex | ConvertTo-Json -Depth 10
} while ($Reindex.status -notin @("completed", "skipped", "failed"))
```

预期为 `completed`。

搜索唯一标识：

```powershell
$TokenSearchBody = @{
    query = $TestToken
    domain = $Domain
    top_k = 5
    mode = "hybrid"
} | ConvertTo-Json

$TokenSearch = Invoke-RestMethod "$RagBase/rag/search" -Method Post `
  -Headers $Headers -ContentType "application/json" -Body $TokenSearchBody
$TokenSearch | ConvertTo-Json -Depth 10
```

预期：

- 至少一个结果包含 `$TestToken`；
- 结果来源为 `_managed/$DocumentId.md`；
- citation 来源与结果来源一致。

### L07：从本地上传扫描 PDF

准备非敏感扫描 PDF：

```powershell
$PdfPath = "D:\test-data\non-sensitive-scan.pdf"
Test-Path -LiteralPath $PdfPath
```

按照 L05 的上传和轮询流程上传 PDF。预期：

- 最终为 `ready` 或 `awaiting_review`，不得为 `failed`；
- preview 中 `metadata.ocr_used=true`；
- `metadata.ocr_model=br-ocr-v1`；
- `metadata.ocr_provider=company_flow`；
- Markdown 正文非空；
- 页码和表格结构与原 PDF 基本一致。

如果为 `awaiting_review`，记录 warnings，并由人工确认后决定是否发布。

### L08：连续请求延迟

```powershell
$Durations = @()
$Failures = 0
1..20 | ForEach-Object {
    try {
        $Elapsed = Measure-Command {
            Invoke-RestMethod "$RagBase/rag/search" -Method Post `
              -Headers $Headers -ContentType "application/json" `
              -Body $TokenSearchBody | Out-Null
        }
        $Durations += $Elapsed.TotalMilliseconds
    } catch {
        $Failures++
    }
}
$Sorted = $Durations | Sort-Object
$P95Index = [Math]::Max(0, [Math]::Ceiling($Sorted.Count * 0.95) - 1)
[PSCustomObject]@{
    Requests = 20
    Failures = $Failures
    AverageMs = [Math]::Round(($Durations | Measure-Object -Average).Average, 2)
    P95Ms = [Math]::Round($Sorted[$P95Index], 2)
    MaxMs = [Math]::Round(($Durations | Measure-Object -Maximum).Maximum, 2)
}
```

预期：失败数为 `0`，无 5xx；内网 P95 建议不超过 5000ms。

### L09：5 路并发搜索

PowerShell 7：

```powershell
$ParallelResults = 1..5 | ForEach-Object -Parallel {
    try {
        $Base = $using:RagBase
        $RequestHeaders = $using:Headers
        $RequestBody = $using:TokenSearchBody
        $Result = Invoke-RestMethod "$Base/rag/search" -Method Post `
          -Headers $RequestHeaders -ContentType "application/json" `
          -Body $RequestBody
        [PSCustomObject]@{ Success = $true; Count = $Result.results.Count; Error = $null }
    } catch {
        [PSCustomObject]@{ Success = $false; Count = 0; Error = $_.Exception.Message }
    }
} -ThrottleLimit 5
$ParallelResults | Format-Table
```

预期：5 个请求全部成功，无超时和 5xx。

### L10：错误响应契约

| 场景 | 操作 | 预期 |
|---|---|---|
| 未知 domain | Search 使用不存在的 domain | `404` |
| 非法 top_k | `top_k=0` | `422` |
| 非法 mode | `mode=invalid` | `422` |
| 错误 metadata | 上传时 metadata 传数组或非法 JSON | `400` |
| 不支持扩展名 | 上传 `.exe` | `400` |
| 不存在的文档 | preview/delete 随机 document ID | `404` |
| 不存在的任务 | 查询随机 reindex job ID | `status=not_found` |

所有错误响应必须是结构化 JSON，不得返回 HTML 错误页或泄露服务器堆栈。

### L11：删除和清理验证

```powershell
$Deleted = Invoke-RestMethod "$RagBase/admin/documents/$DocumentId" `
  -Method Delete -Headers $Headers
$DeleteReindexJobId = $Deleted.reindex_job_id
```

轮询删除触发的 Reindex 到 `completed`，然后再次搜索 `$TestToken`。

预期：测试标识不再命中；其他知识检索正常。

### L12：浏览器直连检查（仅前端需要）

当前服务没有配置 CORS。如果浏览器页面需要直接调用远程 RAG 服务，应测试预检请求：

```powershell
curl.exe -i -X OPTIONS "$RagBase/rag/search" `
  -H "Origin: http://localhost:3000" `
  -H "Access-Control-Request-Method: POST" `
  -H "Access-Control-Request-Headers: content-type,x-api-key"
```

如果没有正确的 `Access-Control-Allow-Origin`，这是当前已知限制。处理方式二选一：

1. 前端通过本地后端或同源网关代理；
2. 服务端增加严格的 CORS 白名单，不允许使用任意来源 `*` 搭配敏感接口。

非浏览器的 Python、Java、Postman、curl 和 due-agent 后端调用不受 CORS 影响。

## 6. 清理

```powershell
if (Test-Path -LiteralPath $MarkdownPath) {
    Remove-Item -LiteralPath $MarkdownPath
}
```

- 删除本计划上传的 Markdown 和 PDF 文档；
- 等待删除触发的 Reindex 完成；
- 不清理服务器整体 ingestion、indexes、Qdrant 或 Redis 目录；
- 保留请求耗时、job ID 和测试报告，但隐藏 API Key。

## 7. 测试报告模板

```markdown
# 本地调用服务器 RAG 服务测试报告

- 测试时间：
- 本地操作系统：
- 服务器地址：
- 服务器 Commit ID：
- 执行人：

| 用例 | 结果 | 耗时/P95 | 关键证据 | 备注 |
|---|---|---:|---|---|
| L01 网络与公开端点 | PASS/FAIL | | | |
| L02 鉴权 | PASS/FAIL | | | |
| L03 Search 契约 | PASS/FAIL | | | |
| L04 Answer 契约 | PASS/FAIL | | | |
| L05 本地 Markdown 上传 | PASS/FAIL | | | |
| L06 发布与检索 | PASS/FAIL | | | |
| L07 本地 PDF/OCR | PASS/FAIL | | | |
| L08 连续请求 | PASS/FAIL | | | |
| L09 并发请求 | PASS/FAIL | | | |
| L10 错误契约 | PASS/FAIL | | | |
| L11 删除清理 | PASS/FAIL | | | |
| L12 浏览器 CORS | PASS/FAIL/N/A | | | |

## 性能数据

- Search 平均延迟：
- Search P95：
- Search 最大延迟：
- Answer 延迟：
- 请求成功率：

## Job 记录

- Markdown ingestion job：
- PDF ingestion job：
- Publish reindex job：
- Delete reindex job：

## 问题清单

1.

## 最终结论

- [ ] 通过，可以进入调用方集成
- [ ] 有条件通过
- [ ] 不通过
```
