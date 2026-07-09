# Parser Config 配置指南

基于 PaddleOCR-VL 的结构化 OCR 输出设计的解析配置。LLM 输出 4 个顶级 key，其中 3 个 JSON 子对象与数据库列一一对应。

## 完整结构

```json
{
  "parser_type": "招商银行",
  "statement_type": "personal",
  "bank_identify_config": {
    "rules": [
      {"block_labels": ["doc_title"], "pattern": "招商银行(.{0,10})交易流水"},
      {"block_labels": ["text"],      "pattern": "账号：[0-9*]{10,}"}
    ],
    "logic": "and"
  },
  "parse_config": {
    "columns": [
      {"header": "交易日期", "field": "date", "clean": "date"},
      {"header": "交易金额", "field": "amount", "clean": "amount"}
    ],
    "income_expense": "amount_sign",
    "date": {"columns": ["交易日期"], "format": "YYYY-MM-DD"},
    "page": {"header_keyword": "交易日期", "multi_page": false},
    "multiline": {"fields": ["counterparty", "summary"], "strategy": "join"}
  },
  "metadata_config": {
    "extract": [
      {"target_field": "account_name", "block_labels": ["text"], "method": "regex", "pattern": "户名[：:]\\s*(.+)"}
    ],
    "normalize": {
      "field_mappings": {"account_name": "account_info.account_name"},
      "fixed_values": {"currency.currency_code": "CNY", "account_info.account_type": "bank_account"},
      "time_range": {"start_field": "start_date", "end_field": "end_date", "display_format": "{start} 至 {end}"},
      "extra_fields": ["account_name", "account_no"]
    }
  }
}
```

## LLM 输出 → DB 映射

| LLM key | DB 列（草稿表/配置表） | 类型 |
|---------|----------------------|------|
| `parser_type` | `parser_type` | varchar |
| `statement_type` | `statement_type` | varchar |
| `bank_identify_config` | `bank_identify_config` | JSON |
| `parse_config` | `parse_config` | JSON |
| `metadata_config` | `metadata_config` | JSON |

**三个 JSON 列组合起来就是完整配置。数据库不需要冗余存完整 JSON。**

---

## 字段详解

### parser_type

银行/支付渠道中文全称。示例：`招商银行`、`微信支付`、`支付宝`。

### statement_type

`personal`（个人） 或 `corporate`（对公）。

---

### bank_identify_config — 银行识别

| 字段 | 类型 | 说明 |
|------|------|------|
| `rules` | array | 多条正则规则，每条含 `block_labels` 和 `pattern` |
| `logic` | string | `"and"`=全部匹配 / `"or"`=任一匹配 |

**rules 元素：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `block_labels` | string[] | OCR 块类型：`doc_title` / `text` / `header` / `footer` |
| `pattern` | string | 正则表达式，匹配块内容。无需 `^` `$` 锚点 |

---

### parse_config — 解析规则

#### parse_config.columns

| 字段 | 类型 | 说明 |
|------|------|------|
| `header` | string | 表头文本 |
| `field` | string | 标准字段：`date` / `amount` / `income` / `expense` / `balance` / `summary` / `counterparty` / `counterparty_account` / `currency` / `direction` / `payment_method` / `reference_no` / `other` |
| `clean` | string | `date` / `amount` / `text` / `none` |
| `combine_with` | string? | 合并字段名 |

每个 field 只能出现一次，必须有 `date` 和 `amount`（或 `income`）列。

#### parse_config.income_expense

```json
"amount_sign"                                        // 金额正负号
"separate_columns"                                   // 独立收支列
{"mode":"value_mapping","column":"列","map":{...}}   // 值映射
{"mode":"direction_column","column":"列"}            // 方向列
```

#### parse_config.date

| 字段 | 说明 |
|------|------|
| `columns` | 日期列表头（单列 1 个，日期+时间 2 个） |
| `format` | `YYYY-MM-DD` / `YYYY/MM/DD` / `YYYY.MM.DD` / `DD/MM/YYYY` / `YYYY-MM-DD HH:mm:ss` / `YYYY年MM月DD日` |

#### parse_config.page

| 字段 | 说明 |
|------|------|
| `header_keyword` | 表头行关键字 |
| `multi_page` | `false`=每页有表头，`true`=仅首页 |

#### parse_config.multiline（可选）

| 字段 | 说明 |
|------|------|
| `fields` | 跨行字段 |
| `strategy` | `join` 或 `split` |

---

### metadata_config — 元数据

#### metadata_config.extract

| 字段 | 说明 |
|------|------|
| `target_field` | 字段名 |
| `block_labels` | OCR 块类型 |
| `method` | `regex` / `keyword` / `direct` |
| `pattern` | 正则或关键字 |
| `format`? | `date_format` / `remove_prefix` / `remove_suffix` |
| `format_param`? | format 参数 |
| `default_value`? | 默认值 |

#### metadata_config.normalize

| 字段 | 说明 |
|------|------|
| `field_mappings` | 原始字段 → 标准路径 |
| `fixed_values` | 固定值 |
| `time_range`? | `{start_field, end_field, display_format}` |
| `field_combinations`? | `[{target_field, source_fields, format}]` |
| `extra_fields`? | 保留到 extra 的字段 |

---

## 示例

### 招商银行

```json
{"parser_type":"招商银行","statement_type":"personal","bank_identify_config":{"rules":[{"block_labels":["doc_title"],"pattern":"招商银行(.{0,10})交易流水"},{"block_labels":["text"],"pattern":"账号：[0-9*]{10,}"}],"logic":"and"},"parse_config":{"columns":[{"header":"交易日期","field":"date","clean":"date"},{"header":"币种","field":"currency","clean":"text"},{"header":"交易金额","field":"amount","clean":"amount"},{"header":"账户余额","field":"balance","clean":"amount"},{"header":"交易摘要","field":"summary","clean":"text"},{"header":"交易对手","field":"counterparty","clean":"text"}],"income_expense":"amount_sign","date":{"columns":["交易日期"],"format":"YYYY-MM-DD"},"page":{"header_keyword":"交易日期","multi_page":false},"multiline":{"fields":["counterparty","summary"],"strategy":"join"}},"metadata_config":{"extract":[{"target_field":"account_name","block_labels":["text"],"method":"regex","pattern":"户名[：:]\\s*(.+)"},{"target_field":"account_no","block_labels":["text"],"method":"regex","pattern":"账号[：:]\\s*([0-9*]+)"}],"normalize":{"field_mappings":{"account_name":"account_info.account_name","account_no":"account_info.account_id"},"fixed_values":{"currency.currency_code":"CNY","account_info.account_type":"bank_account"}}}}
```

### 光大银行

```json
{"parser_type":"光大银行","statement_type":"personal","bank_identify_config":{"rules":[{"block_labels":["doc_title"],"pattern":"光大银行"},{"block_labels":["text"],"pattern":"存入金额|支出金额"}],"logic":"and"},"parse_config":{"columns":[{"header":"记账日期","field":"date","clean":"date"},{"header":"支出金额","field":"expense","clean":"amount"},{"header":"存入金额","field":"income","clean":"amount"},{"header":"账户余额","field":"balance","clean":"amount"},{"header":"对手信息","field":"counterparty","clean":"text"},{"header":"摘要","field":"summary","clean":"text"}],"income_expense":"separate_columns","date":{"columns":["记账日期"],"format":"YYYY-MM-DD"},"page":{"header_keyword":"记账日期","multi_page":false}}}
```

### 微信支付

```json
{"parser_type":"微信支付","statement_type":"personal","bank_identify_config":{"rules":[{"block_labels":["doc_title"],"pattern":"微信支付"},{"block_labels":["text"],"pattern":"交易单号|商户单号"}],"logic":"or"},"parse_config":{"columns":[{"header":"交易时间","field":"date","clean":"date"},{"header":"交易类型","field":"other","clean":"text"},{"header":"交易对方","field":"counterparty","clean":"text"},{"header":"商品","field":"summary","clean":"text"},{"header":"收/支","field":"direction","clean":"text"},{"header":"金额(元)","field":"amount","clean":"amount"},{"header":"支付方式","field":"payment_method","clean":"text"}],"income_expense":{"mode":"direction_column","column":"收/支"},"date":{"columns":["交易时间"],"format":"YYYY-MM-DD HH:mm:ss"},"page":{"header_keyword":"交易时间","multi_page":false}},"metadata_config":{"extract":[{"target_field":"wechat_account","block_labels":["text"],"method":"regex","pattern":"微信号[：:]\\s*(\\S+)"}],"normalize":{"field_mappings":{"wechat_account":"account_info.account_id"},"fixed_values":{"currency.currency_code":"CNY","account_info.account_type":"wechat_account"}}}}
```

## 存储位置

| 阶段 | 表 | 配置相关列 |
|------|-----|-----------|
| 配置生成 | `t_parser_config_draft` | `parser_type`, `statement_type`, `bank_identify_config`, `parse_config`, `metadata_config` |
| 转正后 | `t_parser_config_v2` | 同上 + `config_version`, `is_active`, `grayscale_*` |

---

## LLM 提示词

调用时替换 `{ocr_markdown_text}` 和 `{ocr_parsing_blocks}`。

```
你是一个银行流水解析配置生成专家。根据 PaddleOCR-VL 返回的 PDF 前两页 OCR 结果，生成解析配置 JSON。只输出纯 JSON，不要 Markdown 标记。

## OCR 输入

两份数据：
1. Markdown 文本——含文档标题、元数据和 HTML 表格
2. 结构化块列表——parsing_res_list，每个块有 block_label（类型）和 block_content（文本内容）

block_label 类型：doc_title / text / table / header / footer / seal / image

## 输出 JSON Schema

{
  "parser_type": "银行中文名",
  "statement_type": "personal或corporate",
  "bank_identify_config": {
    "rules": [{"block_labels": ["doc_title"], "pattern": "正则"}],
    "logic": "and或or"
  },
  "parse_config": {
    "columns": [{"header": "表头", "field": "标准字段", "clean": "策略"}],
    "income_expense": "字符串或对象",
    "date": {"columns": ["列名"], "format": "格式"},
    "page": {"header_keyword": "关键字", "multi_page": true或false},
    "multiline": {"fields": ["字段"], "strategy": "join或split"}
  },
  "metadata_config": {
    "extract": [{"target_field": "字段", "block_labels": ["text"], "method": "regex或keyword或direct", "pattern": "正则或关键字"}],
    "normalize": {"field_mappings": {}, "fixed_values": {}, "time_range": {}, "extra_fields": []}
  }
}

## 规则

### parser_type
银行中文全称：招商银行、工商银行、光大银行、交通银行、邮储银行、兴业银行、民生银行、浦发银行、北京银行、华夏银行、支付宝、微信支付 等。

### bank_identify_config
多条正则规则识别银行。doc_title 块优先，text 块辅助。
- 银行名唯一性强（如"招商银行交易流水"不会混淆）→ logic="or"
- 银行名含通用词 → logic="and"，多规则交叉验证
每条 pattern 必须能从 OCR 实际 block_content 中匹配。

### parse_config.columns
field 枚举：date / amount / income / expense / balance / summary / counterparty / counterparty_account / currency / direction / payment_method / reference_no / other
clean 枚举：date / amount / text / none
每个 field 只能出现一次。必须有 date 和 amount（或 income）。

### parse_config.income_expense
- "amount_sign"：金额列有 +/- 号
- "separate_columns"：独立收支两列
- {"mode":"value_mapping","column":"列","map":{"值":"expense"}}：值映射
- {"mode":"direction_column","column":"列"}：方向列

### parse_config.date
format：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD / DD/MM/YYYY / YYYY-MM-DD HH:mm:ss / YYYY年MM月DD日

### parse_config.page
header_keyword：表头行最具辨识度的列名（通常是"交易日期"或"交易时间"）。
multi_page：每页有表头=false，仅首页有=true。

### parse_config.multiline（可选）
表格规整时省略。仅 OCR 明显拆分行时才配。

### metadata_config.extract
- target_field：提取后字段名
- block_labels：搜索的块类型，通常 ["text"]
- method：regex（含捕获组）/ keyword / direct
- pattern：正则或关键字

必须提取的通用字段（根据 OCR 实际内容）： account_name（户名）、account_no（账号）、start_date（起始日期）、end_date（结束日期）

### metadata_config.normalize
- field_mappings：原始字段→标准路径
- fixed_values：银行类 {"currency.currency_code":"CNY","account_info.account_type":"bank_account"}；支付宝 "alipay_account"；微信 "wechat_account"
- time_range：有起止日期时配
- extra_fields：保留到 extra 的原始字段名

## OCR 输入

### Markdown 文本
{ocr_markdown_text}

### 结构化块列表（前两页）
{ocr_parsing_blocks}

只输出 JSON。
```
