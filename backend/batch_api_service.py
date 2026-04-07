# -*- coding: utf-8 -*-
"""
网易 AI Gateway Batch API 服务模块

封装完整的 Batch API 生命周期：
1. 上传 JSONL 文件 → file_id
2. 创建批量任务 → batch_id
3. 轮询任务状态
4. 下载结果文件
5. 取消任务

API Base: https://aigw-int.netease.com/v1
认证: Bearer {app_id}.{app_key}
"""
import os
import json
import time
import asyncio
import tempfile
import traceback
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
import requests


class BatchStatus(str, Enum):
    VALIDATING = "validating"
    IN_PROGRESS = "in_progress"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


@dataclass
class BatchInfo:
    """Batch 任务信息"""
    batch_id: str
    status: BatchStatus
    input_file_id: str = ""
    output_file_id: str = ""
    error_file_id: str = ""
    created_at: int = 0
    completed_at: Optional[int] = None
    failed_at: Optional[int] = None
    expired_at: Optional[int] = None
    request_counts: Dict[str, int] = field(default_factory=lambda: {"completed": 0, "failed": 0, "total": 0})
    errors: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "BatchInfo":
        return cls(
            batch_id=data.get("id", ""),
            status=BatchStatus(data.get("status", "validating")),
            input_file_id=data.get("input_file_id", ""),
            output_file_id=data.get("output_file_id", ""),
            error_file_id=data.get("error_file_id", ""),
            created_at=data.get("created_at", 0),
            completed_at=data.get("completed_at"),
            failed_at=data.get("failed_at"),
            expired_at=data.get("expired_at"),
            request_counts=data.get("request_counts", {"completed": 0, "failed": 0, "total": 0}),
            errors=data.get("errors"),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "status": self.status.value,
            "input_file_id": self.input_file_id,
            "output_file_id": self.output_file_id,
            "error_file_id": self.error_file_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "request_counts": self.request_counts,
            "errors": self.errors,
            "metadata": self.metadata,
        }


class BatchAPIService:
    """网易 AI Gateway Batch API 客户端"""

    # Batch API 使用内网地址
    BATCH_BASE_URL = "https://aigw-int.netease.com/v1"

    def __init__(self, api_key: str):
        """
        Args:
            api_key: 格式为 {app_id}.{app_key}
        """
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
        }

    def _json_headers(self) -> Dict[str, str]:
        return {
            **self.headers,
            "Content-Type": "application/json",
        }

    # ── 1. 文件上传 ──

    def upload_jsonl_file(self, jsonl_content: str, filename: str = "batch_input.jsonl") -> str:
        """
        上传 JSONL 内容到文件 API

        Args:
            jsonl_content: JSONL 格式的字符串
            filename: 文件名

        Returns:
            file_id: 上传后的文件 ID
        """
        url = f"{self.BATCH_BASE_URL}/files"

        # 写入临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
            f.write(jsonl_content)
            temp_path = f.name

        try:
            with open(temp_path, 'rb') as f:
                resp = requests.post(
                    url,
                    headers=self.headers,
                    files={"file": (filename, f, "application/jsonl")},
                    data={
                        "purpose": "batch",
                        "storage_channel": "gcs",
                    },
                    timeout=120,
                )

            if not resp.ok:
                raise Exception(f"文件上传失败 (HTTP {resp.status_code}): {resp.text}")

            data = resp.json()
            file_id = data.get("id", "")
            if not file_id:
                raise Exception(f"文件上传返回无 id: {data}")

            print(f"[BATCH] 文件上传成功: {file_id} ({len(jsonl_content)} bytes)")
            return file_id

        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    # ── 2. 创建批量任务 ──

    def create_batch(
        self,
        input_file_id: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> BatchInfo:
        """
        创建批量任务

        Args:
            input_file_id: 上传的 JSONL 文件 ID
            metadata: 可选元数据

        Returns:
            BatchInfo
        """
        url = f"{self.BATCH_BASE_URL}/batches"

        payload = {
            "input_file_id": input_file_id,
            "completion_window": "24h",
            "endpoint": "/v1/chat/completions",
            "metadata": metadata or {},
        }

        resp = requests.post(url, headers=self._json_headers(), json=payload, timeout=30)

        if not resp.ok:
            raise Exception(f"创建批量任务失败 (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        batch_info = BatchInfo.from_api_response(data)
        print(f"[BATCH] 任务创建成功: {batch_info.batch_id} (status={batch_info.status.value})")
        return batch_info

    # ── 3. 查询任务状态 ──

    def get_batch_status(self, batch_id: str) -> BatchInfo:
        """查询批量任务状态"""
        url = f"{self.BATCH_BASE_URL}/batches/{batch_id}"

        resp = requests.get(url, headers=self.headers, timeout=30)

        if not resp.ok:
            raise Exception(f"查询任务失败 (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        return BatchInfo.from_api_response(data)

    # ── 4. 取消任务 ──

    def cancel_batch(self, batch_id: str) -> BatchInfo:
        """取消批量任务"""
        url = f"{self.BATCH_BASE_URL}/batches/{batch_id}/cancel"

        resp = requests.post(url, headers=self.headers, timeout=30)

        if not resp.ok:
            raise Exception(f"取消任务失败 (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        return BatchInfo.from_api_response(data)

    # ── 5. 列出任务 ──

    def list_batches(self, limit: int = 20, after: Optional[str] = None) -> Dict[str, Any]:
        """列出批量任务"""
        url = f"{self.BATCH_BASE_URL}/batches"
        params = {"limit": limit}
        if after:
            params["after"] = after

        resp = requests.get(url, headers=self.headers, params=params, timeout=30)

        if not resp.ok:
            raise Exception(f"列出任务失败 (HTTP {resp.status_code}): {resp.text}")

        return resp.json()

    # ── 6. 下载结果文件 ──

    def download_results(self, output_file_id: str) -> List[Dict[str, Any]]:
        """
        下载批量任务结果文件并解析

        Args:
            output_file_id: 输出文件 ID

        Returns:
            解析后的结果列表，每条包含 custom_id, response, error
        """
        url = f"{self.BATCH_BASE_URL}/files/{output_file_id}/content"

        resp = requests.get(url, headers=self.headers, timeout=120)

        if not resp.ok:
            raise Exception(f"下载结果文件失败 (HTTP {resp.status_code}): {resp.text}")

        results = []
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[BATCH] 跳过无效结果行: {e}")

        print(f"[BATCH] 下载结果完成: {len(results)} 条")
        return results

    # ── 7. 下载错误文件 ──

    def download_errors(self, error_file_id: str) -> List[Dict[str, Any]]:
        """下载错误文件"""
        if not error_file_id:
            return []
        return self.download_results(error_file_id)


# ── JSONL 生成工具函数 ──

def generate_batch_jsonl(
    rows_data: List[Dict[str, Any]],
    model: str,
    max_output_tokens: int = 64000,
    prompt_cache_enabled: bool = False,
    system_prompt: str = "",
    temperature: float = 0.3,
) -> str:
    """
    将评测数据生成 Batch API 输入 JSONL

    Args:
        rows_data: 列表，每条包含:
            - "custom_id": str  (唯一标识，如 "row-0")
            - "prompt": str     (完整 prompt 文本，用于非 cache 模式)
            - "system_parts": list  (用于 cache 模式的 system 分段)
            - "user_parts": list    (用于 cache 模式的 user 分段)
        model: 模型名称
        max_output_tokens: 最大输出 token
        prompt_cache_enabled: 是否启用 prompt cache
        system_prompt: 系统级 prompt（仅 cache 模式）
        temperature: 温度

    Returns:
        JSONL 格式字符串
    """
    lines = []

    for row in rows_data:
        custom_id = row["custom_id"]

        if prompt_cache_enabled and _is_claude_model(model) and row.get("system_parts"):
            # Claude Prompt Cache 模式：结构化 system + user messages
            body = {
                "model": model,
                "max_tokens": max_output_tokens,
                "temperature": temperature,
                "system": row["system_parts"],
                "messages": row["user_parts"],
            }
        else:
            # 普通模式：单条 user message
            body = {
                "model": model,
                "messages": [{"role": "user", "content": row["prompt"]}],
                "max_tokens": max_output_tokens,
                "temperature": temperature,
            }

        entry = {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": body,
        }

        lines.append(json.dumps(entry, ensure_ascii=False))

    return "\n".join(lines)


def _is_claude_model(model: str) -> bool:
    """判断是否为 Claude 系列模型"""
    model_lower = model.lower()
    return any(kw in model_lower for kw in ["claude", "anthropic"])


def parse_batch_results(
    raw_results: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    解析 Batch API 返回的结果，转为 {custom_id: {content, error, usage}} 映射

    Args:
        raw_results: 从 download_results 获取的原始结果列表

    Returns:
        {custom_id: {"content": str, "error": dict|None, "usage": dict}}
    """
    parsed = {}

    for item in raw_results:
        custom_id = item.get("custom_id", "")
        response = item.get("response", {})
        error = item.get("error")

        if error and error.get("code"):
            parsed[custom_id] = {
                "content": "",
                "error": error,
                "usage": {},
            }
            continue

        body = response.get("body", {})
        status_code = response.get("status_code", 0)

        if status_code != 200:
            parsed[custom_id] = {
                "content": "",
                "error": {"code": str(status_code), "message": f"HTTP {status_code}"},
                "usage": {},
            }
            continue

        choices = body.get("choices", [])
        content = ""
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        parsed[custom_id] = {
            "content": content,
            "error": None,
            "usage": body.get("usage", {}),
        }

    return parsed
