# -*- coding: utf-8 -*-
"""
Batch API 路由端点

提供以下接口:
- POST /api/batch/submit       提交评测数据为 Batch 任务
- GET  /api/batch/{id}/status  查询任务状态
- POST /api/batch/{id}/cancel  取消任务
- GET  /api/batch/{id}/results 获取任务结果
- GET  /api/batch/list         列出所有任务
"""
import json
import os
import time
import traceback
from typing import Optional, List, Dict, Any
from fastapi import UploadFile, File, Form, HTTPException
from pydantic import BaseModel

from batch_api_service import (
    BatchAPIService,
    BatchInfo,
    generate_batch_jsonl,
    parse_batch_results,
)


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BATCH_RESULTS_DIR = os.path.join(_BASE_DIR, "batch_results")


def _ensure_results_dir():
    os.makedirs(BATCH_RESULTS_DIR, exist_ok=True)


def _get_batch_service():
    """获取 BatchAPIService 实例（使用当前 Judge 配置的 API Key）"""
    from main import JUDGE_CFG
    if not JUDGE_CFG.api_key:
        raise ValueError("Judge API Key 未配置")
    return BatchAPIService(api_key=JUDGE_CFG.api_key)


def _save_batch_meta(batch_id: str, meta: Dict[str, Any]):
    """保存 batch 元数据到本地文件"""
    _ensure_results_dir()
    path = os.path.join(BATCH_RESULTS_DIR, f"{batch_id}_meta.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _load_batch_meta(batch_id: str) -> Optional[Dict[str, Any]]:
    """加载 batch 元数据"""
    path = os.path.join(BATCH_RESULTS_DIR, f"{batch_id}_meta.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_batch_results(batch_id: str, results: List[Dict[str, Any]]):
    """保存 batch 结果到本地"""
    _ensure_results_dir()
    path = os.path.join(BATCH_RESULTS_DIR, f"{batch_id}_results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def _load_batch_results(batch_id: str) -> Optional[List[Dict[str, Any]]]:
    """加载缓存的 batch 结果"""
    path = os.path.join(BATCH_RESULTS_DIR, f"{batch_id}_results.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


class BatchSubmitRequest(BaseModel):
    """Batch 提交请求"""
    scenario: str
    dimensions: List[Dict[str, Any]]
    rows_data: List[Dict[str, Any]]  # [{"question": ..., "models": {...}, "context_fields": {...}}]
    file_context: str = ""
    metadata: Dict[str, str] = {}


def register_batch_api_routes(app):
    """注册 Batch API 路由"""

    @app.post("/api/batch/submit")
    async def batch_submit(
        file: UploadFile = File(...),
        scenario: str = Form(...),
        dimensions: str = Form(...),
        mapping: str = Form(...),
        file_type: str = Form("excel"),
        question_prefix: Optional[str] = Form(None),
        file_context: Optional[str] = Form(None),
        batch_size: int = Form(3),
    ):
        """
        提交评测数据为 Batch API 任务

        流程:
        1. 解析上传文件 → 提取评测数据
        2. 为每条/每批数据构建 prompt
        3. 生成 JSONL
        4. 上传文件 → 创建 batch 任务
        5. 返回 batch_id 供前端轮询
        """
        import pandas as pd
        import io

        try:
            from main import JUDGE_CFG
            from prompt_cache_builder import build_cached_prompt_parts, is_claude_model
            from judge_batch_context import build_batch_prompt, _build_prompt_sub_blocks

            # 解析参数
            dims = json.loads(dimensions)
            col_map = json.loads(mapping)
            question_col = col_map.get("question")
            model_cols = col_map.get("models", [])
            context_field_cols = col_map.get("context_fields", [])

            if not question_col or not model_cols:
                return {"error": "mapping 缺少 question 或 models 字段", "status": "failed"}

            # 读取文件
            if file_type == "excel":
                contents = await file.read()
                df = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
            elif file_type == "json":
                from judge_batch_context_api import read_json_file
                df = await read_json_file(file)
            elif file_type == "jsonl":
                from judge_batch_context_api import read_jsonl_file
                df = await read_jsonl_file(file)
            else:
                return {"error": f"不支持的文件类型: {file_type}", "status": "failed"}

            df.columns = [str(c).strip() for c in df.columns]
            valid_context_cols = [c for c in context_field_cols if c in df.columns]

            # 提取数据
            all_rows = []
            for idx, row in df.iterrows():
                raw_text = str(row[question_col])
                if pd.isna(raw_text) or not raw_text.strip() or raw_text.strip() == 'nan':
                    continue

                question = f"{question_prefix}\n\n{raw_text}" if question_prefix else raw_text

                models_data = {}
                for m_col in model_cols:
                    answer = row[m_col]
                    if pd.isna(answer) or str(answer).strip() == 'nan':
                        answer = "No Answer"
                    else:
                        answer = str(answer).strip()
                    models_data[m_col] = answer

                ctx_fields = {}
                for c_col in valid_context_cols:
                    val = row.get(c_col, "")
                    if pd.notna(val):
                        val_str = str(val).strip()
                        if val_str and val_str != 'nan':
                            ctx_fields[c_col] = val_str

                all_rows.append({
                    "row_index": idx,
                    "question": question,
                    "models": models_data,
                    "context_fields": ctx_fields,
                })

            if not all_rows:
                return {"error": "没有有效的数据行", "status": "failed"}

            # 分批构建 JSONL 行
            fc = file_context or ""
            use_cache = JUDGE_CFG.prompt_cache_enabled and is_claude_model(JUDGE_CFG.model)

            jsonl_rows = []
            # 按 batch_size 分组
            for batch_start in range(0, len(all_rows), batch_size):
                batch = all_rows[batch_start:batch_start + batch_size]
                batch_idx = batch_start // batch_size

                batch_data = [
                    {
                        "question": r["question"],
                        "models": r["models"],
                        "context_fields": r.get("context_fields", {}),
                    }
                    for r in batch
                ]

                # 构建 prompt
                prompt = build_batch_prompt(scenario, dims, batch_data, fc)

                row_entry = {
                    "custom_id": f"batch-{batch_idx}",
                    "prompt": prompt,
                }

                # 如果启用 cache，构建结构化分段
                if use_cache:
                    try:
                        dimensions_text, data_block, file_context_block, output_format, batch_count = \
                            _build_prompt_sub_blocks(scenario, dims, batch_data, fc)
                        system_parts, user_msgs = build_cached_prompt_parts(
                            scenario=scenario,
                            dimensions_text=dimensions_text,
                            file_context_block=file_context_block,
                            data_block=data_block,
                            output_format=output_format,
                            batch_count=batch_count,
                        )
                        row_entry["system_parts"] = system_parts
                        row_entry["user_parts"] = user_msgs
                    except Exception as e:
                        print(f"[WARN] Batch cache 构建失败: {e}", flush=True)

                jsonl_rows.append(row_entry)

            # 生成 JSONL
            from judge_caller_factory import get_safe_max_output_tokens
            safe_max_tokens = get_safe_max_output_tokens(JUDGE_CFG.model, JUDGE_CFG.max_output_tokens)
            
            jsonl_content = generate_batch_jsonl(
                rows_data=jsonl_rows,
                model=JUDGE_CFG.model,
                max_output_tokens=safe_max_tokens,
                prompt_cache_enabled=use_cache,
            )

            # 上传并创建任务
            service = _get_batch_service()
            file_id = service.upload_jsonl_file(jsonl_content)
            batch_info = service.create_batch(
                input_file_id=file_id,
                metadata={
                    "scenario": scenario[:100],
                    "total_rows": str(len(all_rows)),
                    "batch_size": str(batch_size),
                    "model": JUDGE_CFG.model,
                    "prompt_cache": str(use_cache),
                },
            )

            # 保存元数据到本地
            _save_batch_meta(batch_info.batch_id, {
                "batch_id": batch_info.batch_id,
                "scenario": scenario,
                "dimensions": dims,
                "model_cols": model_cols,
                "total_rows": len(all_rows),
                "batch_size": batch_size,
                "total_batches": len(jsonl_rows),
                "created_at": time.time(),
                "model": JUDGE_CFG.model,
                "prompt_cache": use_cache,
                "row_mapping": {
                    f"batch-{i}": [r["row_index"] for r in all_rows[i*batch_size:(i+1)*batch_size]]
                    for i in range(len(jsonl_rows))
                },
            })

            print(f"[BATCH] 任务提交成功: {batch_info.batch_id} "
                  f"({len(all_rows)} rows → {len(jsonl_rows)} batches)")

            return {
                "status": "success",
                "batch_id": batch_info.batch_id,
                "total_rows": len(all_rows),
                "total_batches": len(jsonl_rows),
                "batch_info": batch_info.to_dict(),
            }

        except Exception as e:
            print(f"[ERROR] Batch submit failed: {e}\n{traceback.format_exc()}")
            return {"error": str(e), "status": "failed"}

    @app.get("/api/batch/{batch_id}/status")
    async def batch_status(batch_id: str):
        """查询 Batch 任务状态"""
        try:
            service = _get_batch_service()
            info = service.get_batch_status(batch_id)
            meta = _load_batch_meta(batch_id)

            return {
                "status": "success",
                "batch_info": info.to_dict(),
                "meta": meta,
            }
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    @app.post("/api/batch/{batch_id}/cancel")
    async def batch_cancel(batch_id: str):
        """取消 Batch 任务"""
        try:
            service = _get_batch_service()
            info = service.cancel_batch(batch_id)
            return {
                "status": "success",
                "batch_info": info.to_dict(),
            }
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    @app.get("/api/batch/{batch_id}/results")
    async def batch_results(batch_id: str):
        """
        获取 Batch 任务结果

        1. 检查本地缓存
        2. 如果没有缓存，从 API 下载并解析
        3. 转为评测标准格式
        """
        try:
            # 检查缓存
            cached = _load_batch_results(batch_id)
            if cached:
                return {
                    "status": "success",
                    "results": cached,
                    "from_cache": True,
                }

            service = _get_batch_service()
            info = service.get_batch_status(batch_id)

            if info.status.value != "completed":
                return {
                    "error": f"任务尚未完成 (status={info.status.value})",
                    "status": "pending",
                    "batch_info": info.to_dict(),
                }

            if not info.output_file_id:
                return {"error": "任务完成但无输出文件", "status": "failed"}

            # 下载并解析结果
            raw_results = service.download_results(info.output_file_id)
            parsed = parse_batch_results(raw_results)

            # 加载元数据用于结构化映射
            meta = _load_batch_meta(batch_id)

            # 将 batch 结果映射回行级结果
            final_results = _map_batch_to_row_results(parsed, meta)

            # 缓存结果
            _save_batch_results(batch_id, final_results)

            # 下载错误文件（如果有）
            errors = []
            if info.error_file_id:
                try:
                    errors = service.download_errors(info.error_file_id)
                except Exception as e:
                    print(f"[WARN] 下载错误文件失败: {e}")

            return {
                "status": "success",
                "results": final_results,
                "errors": errors,
                "from_cache": False,
                "batch_info": info.to_dict(),
            }

        except Exception as e:
            print(f"[ERROR] Batch results failed: {e}\n{traceback.format_exc()}")
            return {"error": str(e), "status": "failed"}

    @app.get("/api/batch/list")
    async def batch_list(limit: int = 20, after: Optional[str] = None):
        """列出所有 Batch 任务"""
        try:
            service = _get_batch_service()
            data = service.list_batches(limit=limit, after=after)
            return {"status": "success", "data": data}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    print("[INFO] Batch API routes registered: /api/batch/*")


def _map_batch_to_row_results(
    parsed: Dict[str, Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    将 Batch API 返回的 custom_id → content 映射回行级评测结果

    每个 batch 的 content 是一个 JSON，结构如:
    {
        "data_1": { "model_A": {"scores": {...}, "reasoning": "..."}, ... },
        "data_2": { ... },
    }
    """
    from judge_batch_context import parse_batch_response

    results = []

    if not meta:
        # 没有元数据，直接返回原始解析
        for custom_id, item in parsed.items():
            results.append({
                "custom_id": custom_id,
                "content": item.get("content", ""),
                "error": item.get("error"),
            })
        return results

    dimensions = meta.get("dimensions", [])
    model_cols = meta.get("model_cols", [])
    row_mapping = meta.get("row_mapping", {})

    for custom_id, item in parsed.items():
        if item.get("error"):
            # 错误的 batch：为对应行生成失败结果
            row_indices = row_mapping.get(custom_id, [])
            for row_idx in row_indices:
                results.append({
                    "row": row_idx,
                    "evaluations": {},
                    "error": item["error"],
                    "status": "failed",
                })
            continue

        content = item.get("content", "")
        if not content:
            row_indices = row_mapping.get(custom_id, [])
            for row_idx in row_indices:
                results.append({
                    "row": row_idx,
                    "evaluations": {},
                    "error": "Empty response",
                    "status": "failed",
                })
            continue

        # 解析 batch JSON 响应
        row_indices = row_mapping.get(custom_id, [])
        batch_size = len(row_indices)

        # 构建 dummy batch_data 用于 parse
        dummy_batch = [{"models": {m: "" for m in model_cols}} for _ in range(batch_size)]

        try:
            batch_evals = parse_batch_response(content, dummy_batch, dimensions)

            for i, eval_result in enumerate(batch_evals):
                if i < len(row_indices):
                    eval_result["row"] = row_indices[i]
                    eval_result["status"] = "success"
                results.append(eval_result)

        except Exception as e:
            print(f"[ERROR] 解析 batch {custom_id} 结果失败: {e}")
            for row_idx in row_indices:
                results.append({
                    "row": row_idx,
                    "evaluations": {},
                    "error": str(e),
                    "status": "failed",
                })

    results.sort(key=lambda x: x.get("row", 0))
    return results
