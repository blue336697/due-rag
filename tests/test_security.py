"""API 鉴权中间件测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.core.security import AuthMiddleware
from service.core.config import get_managed_knowledge_dir


class SecurityMiddlewareTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.add_middleware(AuthMiddleware, auth_mode="header", api_key_env="TEST_RAG_API_KEY")

        @app.get("/private")
        def private():
            return {"ok": True}

        @app.get("/health")
        def health():
            return {"status": "ok"}

        return TestClient(app)

    def test_header_auth_and_public_health(self):
        with patch.dict(os.environ, {"TEST_RAG_API_KEY": "secret"}):
            with self._client() as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/private").status_code, 401)
                self.assertEqual(
                    client.get("/private", headers={"X-API-Key": "secret"}).status_code,
                    200,
                )

    def test_managed_knowledge_path_is_domain_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "paths": {"managed_knowledge_root": str(Path(tmp) / "managed")},
                "knowledge": {"base_dir": str(Path(tmp) / "source"), "domains": {"bank_stmt": {"enabled": True}}},
            }
            with patch("service.core.config.get_config", return_value=cfg):
                self.assertEqual(
                    Path(get_managed_knowledge_dir("bank_stmt")),
                    (Path(tmp) / "managed" / "bank_stmt").resolve(),
                )
                with self.assertRaises(ValueError):
                    get_managed_knowledge_dir("../escape")


if __name__ == "__main__":
    unittest.main()
