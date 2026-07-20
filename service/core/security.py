"""API 鉴权中间件 — 代码拆分指南 §5 + 高级检索指南 §10.0.1。

auth_mode="none" 仅限本地开发。生产必须 "header" 或网关统一鉴权。
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

_logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {"/health", "/ready", "/docs", "/openapi.json", "/redoc"}


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth_mode: str = "none", api_key_env: str = "RAG_SERVICE_API_KEY"):
        super().__init__(app)
        self._auth_mode = auth_mode
        self._api_key = os.getenv(api_key_env, "")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        if self._auth_mode == "none":
            return await call_next(request)

        if self._auth_mode == "header":
            if not self._api_key:
                return JSONResponse(status_code=500, content={"detail": "API key not configured"})
            if request.headers.get("X-API-Key", "") != self._api_key:
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
            return await call_next(request)

        _logger.warning("Unknown auth_mode: %s", self._auth_mode)
        return JSONResponse(status_code=500, content={"detail": "Invalid authentication mode"})


def create_auth_middleware(app) -> AuthMiddleware:
    from service.core.config import get_config
    cfg = get_config()
    svc = cfg.get("service", {})
    return AuthMiddleware(
        app,
        auth_mode=svc.get("auth_mode", "none"),
        api_key_env=svc.get("api_key_env", "RAG_SERVICE_API_KEY"),
    )
