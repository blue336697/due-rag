"""中文同义词组 — RAG Service 内 canonical source。

供 retrieval/keyword.py、retrieval/vector.py、storage/bm25.py 共用。
迁移后不得在各自模块中复制 _SYNONYM_GROUPS。
"""
from typing import Dict, List

SYNONYM_GROUPS: List[List[str]] = [
    ["对手方", "交易对方", "对方户名", "交易对手", "对方名称"],
    ["交易摘要", "摘要", "备注", "用途", "业务摘要"],
    ["交易金额", "金额", "发生额", "借方发生额", "贷方发生额"],
    ["余额", "联机余额", "可用余额", "账户余额"],
    ["交易日期", "日期", "记账日期", "入账日期", "起息日"],
    ["对方账号", "交易对手账号"],
    ["户名", "账户名称", "账户"],
    ["币种", "货币"],
    ["卡号", "账号", "银行卡号"],
]

def build_synonym_map() -> Dict[str, List[str]]:
    """构建 term -> synonyms 映射表。"""
    mapping: Dict[str, List[str]] = {}
    for group in SYNONYM_GROUPS:
        for term in group:
            mapping[term] = [t for t in group if t != term]
    return mapping

SYNONYM_MAP: Dict[str, List[str]] = build_synonym_map()

def expand_query(query: str) -> str:
    """将查询中的词替换为其同义词组，扩展召回范围。"""
    expanded = query
    for term, synonyms in SYNONYM_MAP.items():
        if term in query:
            expanded += " " + " ".join(synonyms)
    return expanded

def build_synonym_tags(text: str) -> str:
    """为一段文本生成同义词标签字符串，用于 embedding 增强。"""
    tags: List[str] = []
    for group in SYNONYM_GROUPS:
        if any(term in text for term in group):
            tags.extend(group)
    return " ".join(tags) if tags else ""
