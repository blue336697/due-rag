"""RAG Answer 的成功与失败 API 契约测试。"""
from __future__ import annotations

import unittest
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.api.rag import router
from service.models.answer_llm import AnswerGenerationError, generate_answer


def _retrieved() -> list[dict]:
    return [{
        "source": "字段定义/amount.md",
        "heading": "交易金额",
        "content": "交易金额是单笔银行流水的发生金额。",
    }]


def _fake_llm_modules(*, content: str = "", error: Exception | None = None) -> dict:
    openai_module = ModuleType("openai")

    class Completions:
        def create(self, **_kwargs):
            if error is not None:
                raise error
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    class OpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    openai_module.OpenAI = OpenAI
    return {"openai": openai_module}


class AnswerLlmTests(unittest.TestCase):
    def test_llm_success_returns_generated_answer(self):
        modules = _fake_llm_modules(content="交易金额是单笔流水的发生金额 [1]。")
        with patch.dict(sys.modules, modules):
            answer = generate_answer(
                "什么是交易金额？",
                _retrieved(),
                {"base_url": "http://llm.test", "api_key": "test", "model": "test-model"},
            )

        self.assertEqual(answer, "交易金额是单笔流水的发生金额 [1]。")

    def test_llm_failure_raises_instead_of_returning_raw_context(self):
        modules = _fake_llm_modules(error=RuntimeError("upstream-secret-detail"))
        with patch.dict(sys.modules, modules):
            with self.assertLogs("service.models.answer_llm", level="ERROR") as logs:
                with self.assertRaises(AnswerGenerationError):
                    generate_answer(
                        "什么是交易金额？",
                        _retrieved(),
                        {"base_url": "http://llm.test", "api_key": "test", "model": "test-model"},
                    )

        combined_logs = "\n".join(logs.output)
        self.assertIn("RuntimeError", combined_logs)
        self.assertNotIn("upstream-secret-detail", combined_logs)
        self.assertNotIn("--- 片段 [1] ---", combined_logs)


class AnswerApiContractTests(unittest.TestCase):
    def test_generation_failure_is_http_502_with_sanitized_error(self):
        class FailingRetrievalService:
            def answer(self, **_kwargs):
                raise AnswerGenerationError("internal-upstream-secret")

        app = FastAPI()
        app.include_router(router)
        app.state.retrieval_service = FailingRetrievalService()

        with patch("service.api.rag._validate_domain"):
            with TestClient(app) as client:
                response = client.post("/rag/answer", json={
                    "question": "什么是交易金额？",
                    "domain": "bank_stmt",
                })

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {
            "detail": {
                "code": "answer_generation_failed",
                "message": "LLM answer generation failed",
            }
        })
        self.assertNotIn("internal-upstream-secret", response.text)
        self.assertNotIn("--- 片段", response.text)


if __name__ == "__main__":
    unittest.main()
