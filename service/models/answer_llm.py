"""LLM 答案生成 — 基于检索结果调用 LLM 生成带引用答案。

调用方: retrieval/service.py (answer 流程)
从 knowledge_qa.py 迁移，使用独立 LLM 配置。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_QA_SYSTEM_PROMPT = """你是一个支付流水解析知识库的问答助手。请基于以下知识库片段回答用户的问题。

规则：
1. 只使用片段中的信息回答问题，不要编造。
2. 每条关键陈述必须标注来源编号，格式为 [1]、[2] 等。
3. 如果片段信息不足，明确说"知识库信息不足，无法回答该问题"，不要猜测。
4. 如果片段只覆盖了部分问题，先回答已知部分，再说明"以下问题知识库未覆盖：..."。
5. 引用格式：在参考文献部分列出 "[编号] 文件名 - 标题"。"""


def generate_answer(
    question: str,
    retrieved: List[Dict[str, Any]],
    llm_config: Dict[str, Any],
) -> Tuple[str, bool, str]:
    """调用 LLM 生成带引用的答案。

    Args:
        question: 用户问题
        retrieved: 检索到的 chunk 列表
        llm_config: LLM 配置 {"base_url": str, "api_key": str, "model": str, "timeout_seconds": int}

    Returns:
        (answer_text, degraded, error_reason)
        - degraded=True 表示 LLM 调用失败，回退到原始片段
        - error_reason 为空字符串或失败原因
    """
    if not retrieved:
        return "知识库信息不足，无法回答该问题。", False, ""

    # 构建带编号的知识库片段
    chunks_text_parts: List[str] = []
    citations_parts: List[str] = []
    seen_keys: set = set()

    for i, chunk in enumerate(retrieved):
        content = chunk.get("content", "").strip()
        chunks_text_parts.append(f"--- 片段 [{i + 1}] ---\n{content}")

        key = (chunk.get("source", ""), chunk.get("heading", ""))
        if key not in seen_keys:
            citations_parts.append(
                f"[{i + 1}] {chunk.get('source', '')} - {chunk.get('heading', '')}"
            )
            seen_keys.add(key)

    chunks_text = "\n\n".join(chunks_text_parts)
    citations_text = "\n".join(citations_parts)

    user_prompt = f"""知识库片段：

{chunks_text}

参考文献：
{citations_text}

用户问题：{question}"""

    base_url = llm_config.get("base_url", "")
    api_key = llm_config.get("api_key", "")
    model = llm_config.get("model", "")
    timeout = llm_config.get("timeout_seconds", 60)

    # 确保 base_url 以 /v1 结尾
    if base_url and not base_url.rstrip("/").endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"

    try:
        from openai import OpenAI

        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _QA_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        answer = response.choices[0].message.content.strip()
        return answer, False, ""
    except Exception as exc:
        error_reason = f"{type(exc).__name__}: {exc}"
        _logger.warning("LLM 生成回答失败: %s", error_reason)
        return chunks_text, True, error_reason
