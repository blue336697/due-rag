# 中信银行 (citic)

- **parser_type**: `citic`
- **statement_type**: `personal`
- **识别特征**：扫描件 PDF，OCR 后通过 `bank_identify_config` 规则匹配
- **表格特征**：`by_keyword` 表头识别，单列金额，standard 合并模式
- **支持字段**：交易日期、交易金额、余额、交易对手方、交易摘要
