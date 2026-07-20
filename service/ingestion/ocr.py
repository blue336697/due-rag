"""公司 br-ocr-v1 客户端：上传文件、轮询任务并返回结构化页面元素。"""
from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import httpx


class OcrUnavailable(RuntimeError):
    """OCR 服务不可用、任务失败或响应格式错误。"""


def _flow_config(config: Dict[str, Any] | None) -> Dict[str, Any]:
    ocr = config or {}
    flow = ocr.get("flow", ocr)
    return {
        "base_url": str(flow.get("base_url", "http://192.168.160.88:7557")).rstrip("/"),
        "model": str(flow.get("model", "br-ocr-v1")),
        "max_polls": int(flow.get("max_polls", 300)),
        "poll_interval_seconds": float(flow.get("poll_interval_seconds", 1)),
        "connect_timeout_seconds": float(flow.get("connect_timeout_seconds", 30)),
        "read_timeout_seconds": float(flow.get("read_timeout_seconds", 60)),
        "poll_connect_timeout_seconds": float(flow.get("poll_connect_timeout_seconds", 10)),
        "poll_read_timeout_seconds": float(flow.get("poll_read_timeout_seconds", 30)),
        "priority": int(flow.get("priority", 3)),
        "split_method": str(flow.get("split_method", "smart")),
        "chunk_size": int(flow.get("chunk_size", 300)),
    }


def _parse_page_parse_json(data: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    inner = data.get("data") or {}
    page_parse_json = inner.get("page_parse_json")
    if isinstance(page_parse_json, str):
        try:
            page_parse_json = json.loads(page_parse_json.replace("\n", "").replace("\r", ""))
        except json.JSONDecodeError as exc:
            raise OcrUnavailable("OCR page_parse_json 不是合法 JSON") from exc
    if not isinstance(page_parse_json, list):
        raise OcrUnavailable("OCR 结果缺少合法的 page_parse_json")

    pages: Dict[int, List[Dict[str, Any]]] = {}
    for item in page_parse_json:
        if not isinstance(item, dict):
            continue
        try:
            page = int(item.get("page_idx", 1))
        except (TypeError, ValueError):
            page = 1
        pages.setdefault(max(1, page), []).append(item)
    return [pages[page] for page in sorted(pages)]


def call_ocr_parse(file_path: str | Path, config: Dict[str, Any] | None = None) -> List[List[Dict[str, Any]]]:
    """调用 br-ocr-v1，一次上传文件并按页返回 ``page_parse_json``。"""
    path = Path(file_path)
    if not path.is_file():
        raise OcrUnavailable(f"OCR 文件不存在: {path}")

    cfg = _flow_config(config)
    # 保持 due-agent 的 ``OCR + 19 位数字`` Snowflake code 外形，避免服务端格式校验差异。
    task_id = f"OCR{int(time.time() * 1000):013d}{uuid.uuid4().int % 1_000_000:06d}"
    file_bytes = path.read_bytes()
    body = {
        "task_id": task_id,
        "task_type": "parse",
        "priority": cfg["priority"],
        "file_name": path.name,
        "original_file_name": path.name,
        "file_base64": base64.b64encode(file_bytes).decode("ascii"),
        "file_source_type": "base64",
        "model_name": cfg["model"],
        "parser_setting": None,
        "split_setting": {
            "split_method": cfg["split_method"],
            "chunk_size": cfg["chunk_size"],
        },
        "output_queue": None,
        "extra": None,
    }
    timeout = httpx.Timeout(
        connect=cfg["connect_timeout_seconds"],
        read=cfg["read_timeout_seconds"],
        write=cfg["read_timeout_seconds"],
        pool=cfg["connect_timeout_seconds"],
    )
    upload_url = f"{cfg['base_url']}/api/v1/file_parse"
    poll_timeout = httpx.Timeout(
        connect=cfg["poll_connect_timeout_seconds"],
        read=cfg["poll_read_timeout_seconds"],
        write=cfg["poll_read_timeout_seconds"],
        pool=cfg["poll_connect_timeout_seconds"],
    )

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(upload_url, json=body)
            response.raise_for_status()
            upload_payload = response.json()
            upload_code = upload_payload.get("code")
            if upload_code not in (None, 0, 200):
                raise OcrUnavailable(f"OCR 上传失败: {upload_payload.get('msg') or upload_code}")
            upload_data = upload_payload.get("data") or {}
            result_id = upload_data.get("task_uuid") if isinstance(upload_data, dict) else None
            poll_url = f"{cfg['base_url']}/api/v1/result/{result_id or task_id}"

            for poll_index in range(cfg["max_polls"]):
                if cfg["poll_interval_seconds"] > 0:
                    time.sleep(cfg["poll_interval_seconds"])
                try:
                    poll_response = client.get(poll_url, timeout=poll_timeout)
                    poll_response.raise_for_status()
                    payload = poll_response.json()
                except (httpx.HTTPError, ValueError):
                    if poll_index + 1 >= cfg["max_polls"]:
                        raise
                    continue

                inner = payload.get("data") or {}
                status = str(inner.get("task_status", "")).upper()
                if status == "SUCCESS":
                    return _parse_page_parse_json(payload)
                if status in {"FAILED", "ERROR", "CANCELLED"}:
                    detail = inner.get("detail_msg") or inner.get("error_msg") or status
                    raise OcrUnavailable(f"OCR 任务失败: {detail}")
    except OcrUnavailable:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise OcrUnavailable(f"OCR 服务调用失败: {exc}") from exc

    raise OcrUnavailable(f"OCR 任务超时: {cfg['max_polls']} 次轮询")
