# 配置修复策略映射

本文档供 RAG 增强 LLM 修复配置草稿时使用。按校验/回放的 `source_node` 分组，每个错误码包含原因、修复步骤、示例和常见误判。

---

## bank_identify

银行识别配置相关的结构错误。

### CONFIG_BANK_IDENTIFY_EMPTY

- **原因**：`bank_identify_config` 数组为空，LLM 没有生成任何识别规则
- **修复步骤**：
  1. 从 OCR 的 `doc_title` 或 `text` 块中提取银行名称关键词
  2. 构造至少 1 条规则，rule.block_labels 设为 `["doc_title"]` 或 `["text"]`
  3. pattern 用银行全称或中文简称，如 `招商银行`、`中国银行交易流水`
  4. 银行名唯一性强 → logic="or"；含通用词 → logic="and" 并加辅助规则
- **示例**：
  ```json
  {"rules": [{"block_labels": ["doc_title"], "pattern": "招商银行"}, {"block_labels": ["text"], "pattern": "账号：[0-9*]{10,}"}], "logic": "and"}
  ```
- **常见误判**：bank_identify_config 不是数组而是对象；rule 缺少 matchModel 字段（必须为 "parse" 或 "ocr"）

### CONFIG_MATCH_TEXT_EMPTY

- **原因**：某条规则的 `matchText` 为空字符串
- **修复步骤**：
  1. 定位空 matchText 的规则索引
  2. 从 OCR 相应 block_label 的 block_content 中选取能唯一标识该银行的关键词
  3. 不要用过于宽泛的词（如"银行"），要包含银行名称
- **示例**：`"matchText": "${招商银行}"` 或 `"matchText": "${中国银行} and ${交易流水}"`
- **常见误判**：matchText 有内容但空格/换行导致看起来为空；用了 OCR 识别失败的字

### CONFIG_INVALID_MATCH_MODEL

- **原因**：`matchModel` 不在白名单内，合法值为 `parse` 和 `ocr`
- **修复步骤**：
  1. 检查当前 matchModel 值，拼写错误最常见（如 `pars`、`Parse`）
  2. 文本型 PDF → `parse`；扫描件 PDF → `ocr`
- **示例**：`"matchModel": "parse"`
- **常见误判**：用了大写 `"Parse"` / `"OCR"`，白名单是小写

### CONFIG_INVALID_STATEMENT_TYPE

- **原因**：`statement_type` 不在白名单内，合法值为 `personal`、`corporate`、`unknown`
- **修复步骤**：
  1. 检查 OCR 中是否有"对公"、"公司"、"企业"等关键词 → corporate
  2. 检查是否有"个人"、"借记卡"、"储蓄卡"等关键词 → personal
  3. 无法判断 → unknown
- **示例**：`"statement_type": "personal"`
- **常见误判**：写了 `"个人"` / `"对公"`（中文而非英文枚举值）；写了 `"Personal"`（大小写）

---

## headers

表头定位和页级规则相关的结构错误。

### CONFIG_INVALID_HEADER_ROW_TYPE

- **原因**：`header_row_type` 不在白名单内。合法值：`first_row`、`first_row_by_col_count`、`by_keyword`、`first_row_second_table`
- **修复步骤**：
  1. 检查当前值是否有拼写错误
  2. 表头行有明确关键词（如"交易日期"）→ `by_keyword`
  3. 表格第一行就是表头 → `first_row`
  4. 第一行不是表头但列数最多的是表头 → `first_row_by_col_count`
  5. 第二个表格的首行是表头 → `first_row_second_table`
- **示例**：`"header_row_type": "by_keyword"`
- **常见误判**：用 `by_keywords`（多了 s）；用 `keyword`（少了 by_）；用 `auto`

### CONFIG_MISSING_KEYWORD

- **原因**：`header_row_type` 为 `by_keyword` 但未提供 `keyword` 或 `keywords` 字段
- **修复步骤**：
  1. 从 OCR 表格的第一行或前端行中选取最具辨识度的列名
  2. 通常选"交易日期"、"交易时间"、"记账日期"等日期列名，因为每行必有
  3. 设置 `keyword`（单个）或 `keywords`（多个需同时匹配）
- **示例**：
  ```json
  "page_rule": {"header_row_type": "by_keyword", "keyword": "交易日期", "multi_page_header": "no"}
  ```
- **常见误判**：keyword 值写了 OCR 中不存在的中文字；用了太通用的词如"金额"（可能出现在数据行中误判）

### CONFIG_INVALID_MULTI_PAGE_HEADER

- **原因**：`multi_page_header` 不在白名单内，合法值为 `yes` 和 `no`
- **修复步骤**：
  1. 默认写 `"no"`（每页有表头）
  2. 如果确认样本 PDF 只有首页有表头后续页直接是数据 → `"yes"`
- **示例**：`"multi_page_header": "no"`
- **常见误判**：用了 `true` / `false`（布尔值而非字符串）；用了 `"Yes"` / `"No"`（大小写）

---

## amount_fields

字段清洗规则相关的结构错误。

### CONFIG_INVALID_CLEAN_TYPE

- **原因**：`clean_type` 不在白名单内。合法值：`normalize_date`、`remove_spaces`、`clean_amount`、`clean_account`、`multiline_join`、`multiline_split`、`combine_fields`、`combine_fields_normalize_date`、`no_clean`
- **修复步骤**：
  1. 检查当前值是否有拼写错误（最常见：`date` 而非 `normalize_date`，`amount` 而非 `clean_amount`）
  2. 日期字段 → `normalize_date`
  3. 金额字段 → `clean_amount`
  4. 账号字段 → `clean_account`
  5. 普通文本（去除空格换行）→ `remove_spaces`
  6. 不需要清洗 → `no_clean`
  7. 多行文本拼接 → `multiline_join`
  8. 多行文本空格分隔 → `multiline_split`
  9. 合并两个字段 → `combine_fields`
  10. 合并两个字段后日期格式化 → `combine_fields_normalize_date`
- **示例**：`{"field_name": "trade_time", "clean_type": "normalize_date"}`
- **常见误判**：将 `clean_type` 与 `parse_config.columns[].clean` 的简短枚举混淆（后者是 `date`/`amount`/`text`/`none`，前者是完整枚举）

### CONFIG_MISSING_COMBINE_WITH

- **原因**：`clean_type` 为 `combine_fields` 或 `combine_fields_normalize_date` 但未提供 `combine_with` 字段
- **修复步骤**：
  1. 确定被合并的源字段名（通常在 field_rules 中已有定义）
  2. 在对应 field_rule 中增加 `combine_with` 字段，值为源字段名
- **示例**：
  ```json
  {"field_name": "trade_time", "clean_type": "combine_fields_normalize_date", "combine_with": "trade_date", "separator": " "}
  ```
- **常见误判**：combine_with 指向了不存在的字段名；方向反了（合并源和目标 confusion）

---

## field_mapping

列映射和合并策略相关的结构错误。这是 LLM 最常出错的区域。

### CONFIG_INVALID_MERGE_MODE

- **原因**：`merge_mode` 不在白名单内，合法值为 `standard` 和 `interleaved`
- **修复步骤**：
  1. 银行流水（每行一条完整交易）→ `standard`
  2. 微信/支付宝（每笔交易占两行交错排列）→ `interleaved`
  3. 检查当前值是否有拼写错误
- **示例**：`"merge_mode": "standard"`
- **常见误判**：写了 `"normal"` / `"bank"` / `"default"` 而非 `standard`；写了 `"wechat"` / `"alipay"` 而非 `interleaved`

### CONFIG_INVALID_AMOUNT_TYPE

- **原因**：`amount_type` 不在白名单内，合法值为 `A`、`B`、`B_with_label`
- **修复步骤**：
  1. 表格只有一列金额（正负号区分收支）→ `A`
  2. 表格有独立的"收入"和"支出"两列 → `B`
  3. 表格有收支两列且每行有收入/支出文字标签 → `B_with_label`
  4. 常见模式：大多数银行 → `A`；光大银行 → `B`；微信支付 → 用 direction_column 模式 + `B_with_label`
- **示例**：`"amount_type": "A"`
- **常见误判**：用 `"amount_sign"` 或 `"separate"` 等字符串（这些是旧格式 income_expense 的值，不是 amount_type）

### CONFIG_INVALID_STRATEGY

- **原因**：某个 `*Column.strategy` 不在白名单内。合法值：`FIRST_NON_EMPTY`、`COMBINE`、`CONCAT_SPACE`、`CONCAT_COMMA`
- **修复步骤**：
  1. 多列取第一个非空 → `FIRST_NON_EMPTY`
  2. 多列直接拼接 → `COMBINE`
  3. 多列空格拼接 → `CONCAT_SPACE`
  4. 多列逗号拼接 → `CONCAT_COMMA`
- **示例**：`"strategy": "CONCAT_SPACE"`
- **常见误判**：写了小写 `"first_non_empty"`；写了 `"merge"` / `"join"` 等非白名单词

### CONFIG_CORE_FIELD_MISSING

- **原因**：核心字段未在 `merge_config` 的 `*Column.columnNames` 中覆盖。核心字段列表来自 `t_parser_fields` 表 `is_core=1` 的字段
- **修复步骤**：
  1. 定位缺失的核心字段名（错误消息中会指明）
  2. 找到对应的 `*Column`（如 `trade_time` → `timeColumn`，`amount` → `amountColumn`，`counterparty` → `counterpartyColumn`，`trade_type` → `tradeTypeColumn`，`trade_method` → `paymentMethodColumn`）
  3. 在对应 Column 的 `columnNames` 中加入 OCR 表中的实际列名（或该字段的别名）
- **核心字段与 Column 对应关系**：
  - `trade_time` → `timeColumn`
  - `direction` → `directionColumn` 或通过 `determineIncomeExpenseByAmount` / `separateDebitCreditColumnsMapping` 覆盖
  - `amount` → `amountColumn`（A）或 `incomeColumn + expenseColumn`（B）
  - `counterparty` → `counterpartyColumn`
  - `trade_type` → `tradeTypeColumn`（或 `tradeRemarkColumn`，视银行而定）
  - `trade_method` → `paymentMethodColumn`
- **示例**：缺失 `trade_time` → 在 `timeColumn.columnNames` 中加入 `["交易日期", "记账日期"]`
- **常见误判**：columnNames 写了标准字段名而非 OCR 实际列名；列名中有不可见字符；字段别名不匹配（检查 `t_parser_fields` 的 `aliases` 列确认别名列表）

### CONFIG_AMOUNT_DIRECTION_MISSING

- **原因**：`direction` 字段未通过任何方式覆盖。`determineIncomeExpenseByAmount=false` 时，必须有 `incomeExpenseColumn` / `directionColumn` / (`incomeColumn`+`expenseColumn`) / `separateDebitCreditColumnsMapping` 之一
- **修复步骤**：
  1. 如果金额列有正负号 → 设置 `determineIncomeExpenseByAmount: true`
  2. 如果有独立的收/支方向列 → 增加 `directionColumn`，columnNames 为 OCR 中方向列名
  3. 如果收支分列 → 增加 `incomeColumn` 和 `expenseColumn`，各自映射到 OCR 中的对应列
  4. 如果有借方/贷方标签列 → 增加 `separateDebitCreditColumnsMapping`
- **示例**：
  ```json
  "determineIncomeExpenseByAmount": true
  ```
  或
  ```json
  "directionColumn": {"columnNames": ["收/支", "方向"], "strategy": "FIRST_NON_EMPTY"}
  ```
- **常见误判**：directionColumn 的 columnNames 填了 OCR 不存在的列名；设置了 duplicate 覆盖（既设了 directionColumn 又设了 incomeColumn）

### CONFIG_MISSING_DEBIT_CREDIT_MAPPING

- **原因**：`separateDebitCreditColumns=true` 但 `separateDebitCreditColumnsMapping` 缺失或不完整（缺少 `debitColumnName` 或 `creditColumnName`）
- **修复步骤**：
  1. 从 OCR 表中找到借方列名和贷方列名
  2. 补全 `separateDebitCreditColumnsMapping`：`debitColumnName`（支出/借方）、`creditColumnName`（收入/贷方）
- **示例**：
  ```json
  "separateDebitCreditColumnsMapping": {"debitColumnName": "支出金额", "creditColumnName": "存入金额"}
  ```
- **常见误判**：debit/credit 方向反了（借方=支出，贷方=收入）；列名是 OCR 识别不准确的文本

### CONFIG_FIELD_NOT_FOUND

- **原因**：`field_rules` 中引用的 `field_name` 既不在 `merge_config` 列名中，也不在 `metadata_config.extract_rules` 的 `target_field` 中
- **修复步骤**：
  1. 检查 field_name 是否有拼写错误
  2. 如果该字段确实来自表格列 → 确保 merge_config 中有对应的 `*Column.columnNames` 包含该列名
  3. 如果该字段是元数据 → 确保 metadata_config.extract_rules 中有该 target_field
  4. 如果该字段不需要清洗 → 从 field_rules 中移除该条目
- **示例**：field_rules 引用了 `"交易对手"` 但 merge_config 中 counterpartyColumn.columnNames 是 `["对方户名"]` → 改为统一用 `counterparty` 作为 field_name，columnNames 中增加 `"交易对手"`
- **常见误判**：field_name 用了中文列名而非标准字段名（field_name 必须是标准字段名，如 `trade_time` 而非 `"交易时间"`）

### CONFIG_SCOPE_VIOLATION

- **原因**：`merge_config` 的 `*Column.columnNames` 中引用了不允许在当 `statement_type` 中使用的字段。如个人配置引用了 `corporate_only` 的字段（`counterparty_account`、`counterparty_bank`、`nature`、`channel`），或对公配置引用了 `personal_only` 的字段
- **修复步骤**：
  1. 检查错误消息中指出的 forbidden 列名
  2. 如果是个人流水 → 移除 `counterparty_account`、`counterparty_bank`、`nature`、`channel` 相关列
  3. 如果是对公流水 → 将 `counterparty` 拆分为 `counterparty_name`、`counterparty_account`、`counterparty_bank`
- **示例**：个人流水配置中错加了 `counterpartyAccountColumn` → 移除，对手方只保留 `counterpartyColumn`
- **常见误判**：把对公模板直接用于个人流水（或反过来）；别名 `"对方账号"` 被误分配给 `counterpartyColumn`（个人）而非 `counterpartyAccountColumn`（对公）

---

## metadata

元数据提取相关的结构错误。

### CONFIG_INVALID_EXTRACT_METHOD

- **原因**：`extract_method` 不在白名单内，合法值为 `regex`、`keyword`、`direct`
- **修复步骤**：
  1. 用正则表达式提取 → `regex`（如提取账号、日期）
  2. 用关键字匹配 → `keyword`（如找到"户名"后面的文本）
  3. 直接赋值固定值 → `direct`
- **示例**：`"method": "regex"`
- **常见误判**：写了 `"正则"` / `"pattern"` 而非 `regex`；写了 `"关键词"` 而非 `keyword`

### CONFIG_INVALID_FORMAT_METHOD

- **原因**：`format_method` 不在白名单内。合法值：`none`、`date_format`、`date_time_format`、`remove_prefix`、`remove_suffix`
- **修复步骤**：
  1. 日期类字段（如 8 位数字日期）→ `date_format`
  2. 日期时间字段 → `date_time_format`
  3. 需去掉前缀 → `remove_prefix`，配合 `format_param` 指定前缀
  4. 需去掉后缀 → `remove_suffix`，配合 `format_param` 指定后缀
  5. 无需格式化 → 省略不写或 `none`
- **示例**：`{"format_method": "date_format", "format_param": "yyyyMMdd"}`
- **常见误判**：写了 `"date"` 而非 `date_format`；写了 `"trim"` 而非 `remove_prefix`

### CONFIG_MISSING_EXTRACT_METHOD

- **原因**：`extract_rule` 有值但 `extract_method` 为空
- **修复步骤**：
  1. 如果提供了正则表达式 → method 填 `regex`
  2. 如果提供了关键词 → method 填 `keyword`
  3. 如果是直接赋值 → method 填 `direct`
- **示例**：`{"target_field": "account_name", "extract_method": "regex", "extract_rule": "户名[：:]\\s*(.+)"}`
- **常见误判**：写了 extract_rule 但忘记写 extract_method

### CONFIG_INVALID_FIELD_MAPPING

- **原因**：`normalize_rule.field_mappings` 的 value 不是有效的点分路径（不含 `.`）
- **修复步骤**：
  1. 点分路径格式：`section.field_name`，如 `account_info.account_name`、`currency.currency_code`
  2. 标准路径参考：
     - 账户类 → `account_info.account_name`、`account_info.account_id`、`account_info.bank_name`
     - 时间类 → `time_range.start_date`、`time_range.end_date`
     - 币种 → `currency.currency_code`
- **示例**：`"account_name": "account_info.account_name"`
- **常见误判**：value 写了平铺的字段名（`"account_name"` 而非 `"account_info.account_name"`）；用了中文路径

### CONFIG_MISSING_TARGET_FIELD

- **原因**：`extract_rule` 中缺少 `target_field` 字段
- **修复步骤**：
  1. 确认要提取的元数据字段名（如 `account_name`、`account_no`、`start_date`、`end_date`）
  2. 为每条 extract_rule 补上 `target_field`
- **示例**：`{"target_field": "account_name", "block_labels": ["text"], "method": "regex", "pattern": "户名[：:]\\s*(.+)"}`
- **常见误判**：混淆 `target_field`（提取后的字段名）和 `extract_rule`（提取用的正则/关键词）

---

## schema

Pydantic 结构级错误，无 `source_node`。

### CONFIG_SCHEMA_INVALID

- **原因**：配置 JSON 不符合 Pydantic schema 的结构要求，如缺失必填字段、字段类型错误
- **修复步骤**：
  1. 查看错误消息中的具体 `loc`（位置）和 `msg`（原因）
  2. 常见问题：`draft_configs` 缺失；`merge_config` 中缺少 `timeColumn`；`clean_config` 中缺少 `page_rule`；数组类型字段传了对象
  3. 按错误消息逐个字段修正
- **示例**：`loc=draft_configs.merge_config.timeColumn, msg=Field required` → 补充 timeColumn 定义
- **常见误判**：JSON 格式错误（多余的逗号、引号不匹配）；字段名拼写与 schema 不一致（如 `mergeConfig` 而非 `merge_config`）

### CONFIG_INCOMPLETE

- **原因**：配置缺少关键部分，有必填的配置块未提供
- **修复步骤**：
  1. 检查是否缺少 `bank_identify_config`（必需）、`clean_config`（必需含 `page_rule`）、`metadata_config`（必需含 `extract_rules`）、`merge_config`（必需）
  2. 补全缺失的配置块
- **示例**：缺少 metadata_config → 补充 extract 和 normalize 配置
- **常见误判**：某个配置块是空对象 `{}` 而非 null，但缺少必需子字段

---

## replay

回放验证错误。草稿配置通过 schema 校验后，用其解析原始 PDF 时产生的错误。所有错误 `source_node` 均为 `field_mapping`。

### REPLAY_NO_RECORDS

- **原因**：草稿配置解析出的交易记录数为 0，通常因为列映射完全不匹配
- **修复步骤**：
  1. 检查 `merge_config` 中 `*Column.columnNames` 是否与 OCR 表格的实际列名一致
  2. 检查 `header_row_type` 和 `keyword` 是否正确找到了表头行——如果表头定位错了，所有列都匹配不上
  3. 检查 `date.format` 是否与实际日期格式一致
  4. 用回放错误消息中的提示交叉验证列名
- **示例**：OCR 表头是"交易日期"但 `timeColumn.columnNames` 写了"日期" → 改为 `["交易日期", "记账日期"]`
- **常见误判**：header_keyword 匹配到了非表头行的文本；columnNames 有 OCR 中不存在的列名

### REPLAY_DATE_MISSING

- **原因**：超过 50% 的记录缺少交易日期，`timeColumn` 映射不正确
- **修复步骤**：
  1. 检查 OCR 表格的日期列名是什么（如"交易日期"、"记账日期"、"交易时间"）
  2. 更新 `timeColumn.columnNames` 为实际的 OCR 列名
  3. 检查 `date.format` 是否正确（格式不匹配也会导致日期解析失败）
  4. 如果日期分两列（日期+时间），确保使用 `combine_fields` 或 `strategy: "COMBINE"`
- **示例**：`"timeColumn": {"columnNames": ["交易日期"], "strategy": "FIRST_NON_EMPTY"}`
- **常见误判**：日期格式写了 `YYYY-MM-DD` 但 OCR 输出是 `YYYY/MM/DD`；日期时间合并策略写成了 `FIRST_NON_EMPTY` 而非 `CONCAT_SPACE`

### REPLAY_AMOUNT_MISSING

- **原因**：超过 50% 的记录缺少金额，`amountColumn`（或 `incomeColumn`/`expenseColumn`）映射不正确
- **修复步骤**：
  1. 确认 OCR 表格的金额列名
  2. `amount_type=A` → 检查 `amountColumn.columnNames`
  3. `amount_type=B` → 检查 `incomeColumn.columnNames` 和 `expenseColumn.columnNames`
  4. 检查 `clean_config.field_rules` 中金额字段的 `clean_type` 是否为 `clean_amount`
- **示例**：`"amountColumn": {"columnNames": ["交易金额", "金额(元)"], "strategy": "FIRST_NON_EMPTY"}`
- **常见误判**：金额列名匹配上了但 clean_type 不对导致金额清洗失败；收支分列时只配了 amountColumn 没配 incomeColumn/expenseColumn

### REPLAY_COUNTERPARTY_LOW

- **原因**：超过 50% 的记录缺少交易对手方，`counterpartyColumn` 映射可能不正确
- **修复步骤**：
  1. 检查 OCR 表格中对手方列的实际名称（常见："交易对方"、"对方户名"、"对手信息"、"对方账户名"）
  2. 更新 `counterpartyColumn.columnNames`
  3. 如果是跨行合并的对手方信息，检查 `multiline_fields` 是否包含 `counterparty` 并设了正确的 `merge_mode`
- **示例**：`"counterpartyColumn": {"columnNames": ["交易对手", "对方户名"], "strategy": "FIRST_NON_EMPTY"}`
- **常见误判**：对手方列名被映射到了 `tradeRemarkColumn`（两者容易混淆）；对公流水对手方拆分到 `counterparty_name` + `counterparty_account` 但配置中只配了其中一个

### REPLAY_DIRECTION_LOW

- **原因**：超过 30% 的记录缺少收支方向，方向判断逻辑有问题
- **修复步骤**：
  1. 确认实际的收支表示方式：
     - 金额有 +/- 号 → `determineIncomeExpenseByAmount: true`
     - 有独立方向列 → 配 `directionColumn`
     - 有收支两列 → 配 `incomeColumn` + `expenseColumn`
     - 有借/贷标签 → 配 `separateDebitCreditColumnsMapping`
  2. 检查 `amount_type` 是否与收支表示方式一致
- **示例**：金额正负号区分收支 → `"determineIncomeExpenseByAmount": true`
- **常见误判**：方向列的值不是标准映射（如"借"/"贷"、"收入"/"支出"、"存入"/"支取"）但没配 `incomeExpenseValueMapping`

### REPLAY_PARSE_ERROR

- **原因**：草稿配置在解析过程中抛出异常，无法完成解析
- **修复步骤**：
  1. 查看 `error` 字段中的具体异常信息
  2. 常见原因：配置 JSON 解析失败、merge_config 结构与 parser 期望不一致、columnNames 引用了不存在的列
  3. 根据异常信息定向修复对应配置块
- **示例**：`KeyError: 'timeColumn'` → merge_config 缺少 timeColumn 定义
- **常见误判**：配置 block 整体缺失而非字段错误；`parse_config` 格式与 `merge_config` 格式混淆
