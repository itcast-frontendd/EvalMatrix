# -*- coding: utf-8 -*-
"""
批量上下文评分API端点（v2）
支持JSONL/JSON/Excel，每N行一批调用模型

v2 变更:
- mapping 新增 "context_fields" 可选字段（行级附加上下文）
- 新增 "file_context" 参数（文件级上下文，如完整原始转录）
- 新增 "file_context_field" 映射字段（从JSONL中读取file_context）
- 向后兼容：不传新参数时行为与v1完全一致

mapping格式（v2）:
{
    "question": "asr_text",                           // 主输入字段
    "models": ["translation_model_A", "translation_model_B"],  // 待评分字段（支持多模型对比）
    "context_fields": ["reference_text", "context_before", "context_after"]  // 可选，行级上下文
}
"""
import json
import io
import traceback
from typing import List, Dict, Any, Optional
from fastapi import UploadFile, File, Form
import pandas as pd

from judge_batch_context import process_evaluation_with_batch_context


async def judge_only_excel_batch_context(
    file: UploadFile = File(...),
    scenario: str = Form(...),
    dimensions: str = Form(...),
    mapping: str = Form(...),
    file_type: str = Form("excel"),
    question_prefix: Optional[str] = Form(None),
    batch_size: int = Form(3),
    concurrency: int = Form(3),
    file_context: Optional[str] = Form(None),
    eval_session_id: Optional[str] = Form(None),
):
    """
    批量上下文评分API

    参数:
    - file: 上传文件（Excel/JSON/JSONL）
    - scenario: 评测场景描述
    - dimensions: 评分维度 JSON 字符串
    - mapping: 字段映射 JSON 字符串
        {
          "question": "字段名",               // 主输入（必填）
          "models": ["字段1", "字段2"],       // 待评分字段（必填，支持多模型对比）
          "context_fields": ["字段A", "字段B"]  // 行级附加上下文（可选）
        }
    - file_type: excel | json | jsonl
    - question_prefix: 问题前缀（可选）
    - batch_size: 每批数据条数（默认3）
    - concurrency: 并发批次数（默认3）
    - file_context: 文件级上下文文本（可选，如完整转录/参考文档）
    """

    # ── 1. 解析参数 ──
    try:
        dims = json.loads(dimensions)
        col_map = json.loads(mapping)

        question_col = col_map.get("question")
        model_cols = col_map.get("models", [])
        context_field_cols = col_map.get("context_fields", [])  # v2 新增

        if not question_col or not model_cols:
            raise ValueError("mapping缺少question或models字段")

        print(f"[INFO] Batch Context Judge v2: file_type={file_type}, "
              f"batch_size={batch_size}, concurrency={concurrency}")
        print(f"[DEBUG] Question: {question_col}")
        print(f"[DEBUG] Models: {model_cols}")
        print(f"[DEBUG] Context fields: {context_field_cols}")
        print(f"[DEBUG] File context: {len(file_context or '')} chars")

    except Exception as e:
        print(f"[ERROR] 参数解析失败: {e}")
        return {"error": f"参数解析失败: {str(e)}", "status": "failed"}

    # ── 2. 读取文件 ──
    try:
        if file_type == "excel":
            contents = await file.read()
            df = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
        elif file_type == "json":
            df = await read_json_file(file)
        elif file_type == "jsonl":
            df = await read_jsonl_file(file)
        else:
            return {"error": f"不支持的文件类型: {file_type}", "status": "failed"}

        df.columns = [str(c).strip() for c in df.columns]

        print(f"[INFO] 成功加载文件: {len(df)}行数据")
        print(f"[DEBUG] 可用字段: {df.columns.tolist()}")

    except Exception as e:
        print(f"[ERROR] 文件读取失败: {e}\n{traceback.format_exc()}")
        return {"error": f"文件读取失败: {str(e)}", "status": "failed"}

    # ── 3. 验证列存在 ──
    required_cols = [question_col] + model_cols
    # context_fields 是可选的，只验证mapping中指定且存在于数据中的
    valid_context_cols = [c for c in context_field_cols if c in df.columns]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        return {
            "error": f"以下列不存在: {missing} (可用列: {df.columns.tolist()})",
            "status": "failed",
        }

    # 如果指定了context字段但不存在，给出warning
    missing_ctx = [c for c in context_field_cols if c not in df.columns]
    if missing_ctx:
        print(f"[WARN] 以下context_fields不存在，将被忽略: {missing_ctx}")

    # ── 4. 准备数据 ──
    all_rows_data = []

    for idx, row in df.iterrows():
        raw_text = str(row[question_col])
        if pd.isna(raw_text) or not raw_text.strip() or raw_text.strip() == 'nan':
            continue

        question = apply_question_prefix(raw_text, question_prefix)

        # 待评分的模型/字段
        models_data = {}
        for m_col in model_cols:
            answer = row[m_col]
            if pd.isna(answer):
                answer = "No Answer"
            else:
                answer = str(answer).strip()
                if answer == 'nan':
                    answer = "No Answer"
            models_data[m_col] = answer

        # 行级上下文字段
        context_fields = {}
        for c_col in valid_context_cols:
            val = row.get(c_col, "")
            if pd.notna(val):
                val_str = str(val).strip()
                if val_str and val_str != 'nan':
                    context_fields[c_col] = val_str

        all_rows_data.append({
            "row_index": idx,
            "question": question,
            "models": models_data,
            "context_fields": context_fields,
        })

    if not all_rows_data:
        return {"error": "没有有效的数据行", "status": "failed"}

    print(f"[INFO] 准备完成: {len(all_rows_data)}条有效数据")

    # ── 5. 批量上下文评分 ──
    try:
        from judge_caller_factory import create_judge_caller
        from main import JUDGE_CFG
        
        # 导入 abort 机制
        abort_checker = None
        if eval_session_id:
            try:
                from main import is_eval_aborted, cleanup_eval_session
                abort_checker = lambda: is_eval_aborted(eval_session_id)
                print(f"[INFO] Abort support enabled for session {eval_session_id}", flush=True)
            except ImportError:
                pass
        
        # 根据配置决定是否启用 prompt cache
        use_cache = JUDGE_CFG.prompt_cache_enabled
        print(f"[DEBUG] prompt_cache_enabled={use_cache}, model={JUDGE_CFG.model}", flush=True)
        judge_caller = create_judge_caller(use_prompt_cache=use_cache)

        results = await process_evaluation_with_batch_context(
            scenario=scenario,
            dimensions=dims,
            all_rows_data=all_rows_data,
            judge_caller=judge_caller,
            batch_size=batch_size,
            concurrency=concurrency,
            file_context=file_context or "",
            prompt_cache_enabled=use_cache,
            abort_checker=abort_checker,
        )

    except Exception as e:
        print(f"[ERROR] 批量评分失败: {e}\n{traceback.format_exc()}")
        return {"error": f"批量评分失败: {str(e)}", "status": "failed"}

    # ── 6. 聚合结果 ──
    aggregated = aggregate_results(results, model_cols, dims)

    # 检查是否被中断
    was_aborted = False
    if eval_session_id:
        try:
            from main import is_eval_aborted, cleanup_eval_session
            was_aborted = is_eval_aborted(eval_session_id)
            cleanup_eval_session(eval_session_id)
        except ImportError:
            pass

    return {
        "status": "aborted" if was_aborted else "success",
        "results": aggregated,
        "batch_info": {
            "total_rows": len(all_rows_data),
            "batch_size": batch_size,
            "total_batches": (len(all_rows_data) + batch_size - 1) // batch_size,
        },
        "aborted": was_aborted,
    }


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

def apply_question_prefix(text: str, prefix: Optional[str]) -> str:
    if prefix:
        return f"{prefix}\n\n{text}"
    return text


def aggregate_results(
    results: List[Dict[str, Any]],
    model_cols: List[str],
    dims: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """聚合所有行的评分结果"""
    aggregated = {}

    for model_name in model_cols:
        model_runs = []
        all_answers = []

        for result in results:
            evals = result.get("evaluations", {})
            model_eval = evals.get(model_name, {})

            run_entry = {
                "question": result.get("question", ""),
                "answer": result.get("answers", {}).get(model_name, "No Answer"),
                "scores": model_eval.get("scores", {}),
                "reasoning": model_eval.get("reasoning", ""),
            }

            model_runs.append(run_entry)
            all_answers.append(run_entry["answer"])

        avg_scores = {}
        for dim in dims:
            dim_name = dim["name"]
            dim_type = dim.get("type", "scale")

            valid_scores = [
                run["scores"].get(dim_name)
                for run in model_runs
                if run["scores"].get(dim_name) is not None
            ]

            if not valid_scores:
                avg_scores[dim_name] = 0
            elif dim_type == "scale":
                nums = [float(s) for s in valid_scores if isinstance(s, (int, float))]
                avg_scores[dim_name] = round(sum(nums) / len(nums), 1) if nums else 0
            elif dim_type == "binary":
                true_cnt = sum(1 for s in valid_scores if s is True or str(s).lower() in ["true", "1", "yes"])
                avg_scores[dim_name] = round(true_cnt / len(valid_scores), 2)
            else:
                avg_scores[dim_name] = 0

        aggregated[model_name] = {
            "avgScores": avg_scores,
            "runs": model_runs,
            "sampleReasoning": model_runs[-1]["reasoning"] if model_runs else "",
            "sampleAnswer": model_runs[-1]["answer"] if model_runs else "",
            "allAnswers": all_answers,
            "allQuestions": [run["question"] for run in model_runs],
        }

    return aggregated


async def read_json_file(file: UploadFile) -> pd.DataFrame:
    contents = await file.read()
    data = json.loads(contents)
    if isinstance(data, list):
        return pd.DataFrame(data)
    elif isinstance(data, dict) and "data" in data:
        return pd.DataFrame(data["data"])
    else:
        raise ValueError("JSON格式需要是数组或包含'data'字段的对象")


async def read_jsonl_file(file: UploadFile) -> pd.DataFrame:
    contents = await file.read()
    text = contents.decode("utf-8")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    records = []
    for line in lines:
        try:
            record = json.loads(line)
            flattened = flatten_nested_dict(record)
            records.append(flattened)
        except json.JSONDecodeError as e:
            print(f"[WARN] 跳过无效JSON行: {line[:100]}... ({e})")
    if not records:
        raise ValueError("JSONL文件中没有有效数据")
    return pd.DataFrame(records)


def flatten_nested_dict(d: Dict, parent_key: str = "", sep: str = ".") -> Dict:
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_nested_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            items.append((new_key, json.dumps(v, ensure_ascii=False)))
        else:
            items.append((new_key, v))
    return dict(items)


# ──────────────────────────────────────────────
# 注册API路由
# ──────────────────────────────────────────────

def register_batch_context_routes(app):
    """注册批量上下文评分API到FastAPI"""

    @app.post("/api/judge-only-excel-batch")
    async def api_judge_only_excel_batch_context(
        file: UploadFile = File(...),
        scenario: str = Form(...),
        dimensions: str = Form(...),
        mapping: str = Form(...),
        file_type: str = Form("excel"),
        question_prefix: Optional[str] = Form(None),
        batch_size: int = Form(3),
        concurrency: int = Form(3),
        file_context: Optional[str] = Form(None),
        eval_session_id: Optional[str] = Form(None),
    ):
        """
        批量上下文评分API（v2）

        新增参数:
        - mapping.context_fields: 行级附加上下文字段列表
        - file_context: 文件级上下文（如完整转录文本）
        - eval_session_id: 评测会话ID（用于中断控制）
        """
        return await judge_only_excel_batch_context(
            file, scenario, dimensions, mapping, file_type,
            question_prefix, batch_size, concurrency, file_context,
            eval_session_id,
        )

    @app.post("/api/preview-prompt")
    async def api_preview_prompt(
        file: UploadFile = File(...),
        scenario: str = Form(...),
        dimensions: str = Form(...),
        mapping: str = Form(...),
        file_type: str = Form("excel"),
        question_prefix: Optional[str] = Form(None),
        batch_size: int = Form(3),
        file_context: Optional[str] = Form(None),
        preview_rows: int = Form(2),
        api_version: str = Form("v2"),
    ):
        """
        预览实际发送给模型的 prompt 请求体。
        读取文件前 preview_rows 行数据，用与评测完全相同的逻辑构建 prompt。
        
        返回:
        - prompt: 完整拼接后的 prompt 文本
        - model_config: 当前 Judge 模型配置
        - template_source: 模板来源 (file / builtin)
        - data_rows_used: 实际使用的数据行数
        - request_body: 模拟的完整 HTTP 请求体结构
        """
        # ── 1. 解析参数 ──
        try:
            dims = json.loads(dimensions)
            col_map = json.loads(mapping)
            question_col = col_map.get("question")
            model_cols = col_map.get("models", [])
            context_field_cols = col_map.get("context_fields", [])
            if not question_col or not model_cols:
                raise ValueError("mapping 缺少 question 或 models 字段")
        except Exception as e:
            return {"error": f"参数解析失败: {str(e)}", "status": "failed"}

        # ── 2. 读取文件 ──
        try:
            if file_type == "excel":
                contents = await file.read()
                df = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
            elif file_type == "json":
                df = await read_json_file(file)
            elif file_type == "jsonl":
                df = await read_jsonl_file(file)
            else:
                return {"error": f"不支持的文件类型: {file_type}", "status": "failed"}
            df.columns = [str(c).strip() for c in df.columns]
        except Exception as e:
            return {"error": f"文件读取失败: {str(e)}", "status": "failed"}

        # ── 3. 验证列存在 ──
        required_cols = [question_col] + model_cols
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            return {"error": f"以下列不存在: {missing}", "status": "failed"}
        valid_context_cols = [c for c in context_field_cols if c in df.columns]

        # ── 4. 准备前 N 行数据 ──
        rows_collected = []
        for idx, row in df.iterrows():
            raw_text = str(row[question_col])
            if pd.isna(raw_text) or not raw_text.strip() or raw_text.strip() == 'nan':
                continue
            question = apply_question_prefix(raw_text, question_prefix)
            models_data = {}
            for m_col in model_cols:
                answer = row[m_col]
                if pd.isna(answer):
                    answer = "No Answer"
                else:
                    answer = str(answer).strip()
                    if answer == 'nan':
                        answer = "No Answer"
                models_data[m_col] = answer
            context_fields = {}
            for c_col in valid_context_cols:
                val = row.get(c_col, "")
                if pd.notna(val):
                    val_str = str(val).strip()
                    if val_str and val_str != 'nan':
                        context_fields[c_col] = val_str
            rows_collected.append({
                "row_index": idx,
                "question": question,
                "models": models_data,
                "context_fields": context_fields,
            })
            if len(rows_collected) >= preview_rows:
                break

        if not rows_collected:
            return {"error": "没有有效的数据行", "status": "failed"}

        # ── 5. 构建 prompt ──
        from judge_batch_context import build_batch_prompt, _load_batch_prompt_template
        from main import JUDGE_CFG, get_judge_config

        # 准备 batch_data (同评测逻辑)
        batch_data = [
            {
                "question": row["question"],
                "models": row["models"],
                "context_fields": row.get("context_fields", {}),
            }
            for row in rows_collected
        ]

        actual_batch_size = min(batch_size, len(batch_data))
        first_batch = batch_data[:actual_batch_size]

        if api_version == "v2":
            prompt = build_batch_prompt(
                scenario=scenario,
                dimensions=dims,
                batch_data=first_batch,
                file_context=file_context or "",
            )
        else:
            # V1: 使用 main.py 的 load_judge_prompt
            from main import load_judge_prompt
            products_text = ""
            for data_item in first_batch:
                for m_col, ans in data_item["models"].items():
                    products_text += (
                        f"Product ID: {m_col}\n"
                        f"Input: {data_item['question']}\n"
                        f"Answer: {ans}\n\n"
                    )
            dimensions_text = json.dumps(dims, indent=2, ensure_ascii=False)
            prompt = load_judge_prompt(
                scenario, dimensions_text, products_text,
                question=first_batch[0]["question"] if first_batch else None,
            )

        # ── 6. 获取模型配置 & 构建模拟请求体 ──
        judge_config = get_judge_config()
        template_obj = _load_batch_prompt_template()
        template_source = "file (judge_batch_prompt.txt)" if template_obj else "builtin (default)"
        if api_version != "v2":
            template_source = "file (judge_prompt.txt) or builtin"

        # 构建模拟的完整请求体（和 judge_caller 发送的一致）
        from judge_caller_factory import get_safe_max_output_tokens, _supports_temperature
        model_name = JUDGE_CFG.model
        safe_max_tokens = get_safe_max_output_tokens(model_name, JUDGE_CFG.max_output_tokens)
        temp_param = {"temperature": 0.2} if _supports_temperature(model_name) else {}

        simulated_request_body = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": safe_max_tokens,
            **temp_param,
        }

        # 如果启用 prompt cache 且是 Claude, 展示 cache 模式的结构
        cache_request_body = None
        if JUDGE_CFG.prompt_cache_enabled:
            try:
                from prompt_cache_builder import build_cached_prompt_parts, is_claude_model
                if is_claude_model(model_name):
                    from judge_batch_context import _build_prompt_sub_blocks
                    dims_text, data_block, fc_block, output_fmt, bc = \
                        _build_prompt_sub_blocks(scenario, dims, first_batch, file_context or "")
                    system_parts, user_messages = build_cached_prompt_parts(
                        scenario=scenario,
                        dimensions_text=dims_text,
                        file_context_block=fc_block,
                        data_block=data_block,
                        output_format=output_fmt,
                        batch_count=bc,
                    )
                    cache_request_body = {
                        "model": model_name,
                        "system": system_parts,
                        "messages": user_messages,
                        "max_tokens": safe_max_tokens,
                        **temp_param,
                    }
            except Exception as e:
                cache_request_body = {"error": f"Cache prompt 构建失败: {str(e)}"}

        return {
            "status": "success",
            "prompt": prompt,
            "prompt_char_count": len(prompt),
            "prompt_estimated_tokens": len(prompt) // 4,
            "template_source": template_source,
            "data_rows_used": len(first_batch),
            "total_data_rows": len(df),
            "api_version": api_version,
            "model_config": {
                "model": model_name,
                "api_url": judge_config["url"] if judge_config else "(未配置)",
                "max_output_tokens": safe_max_tokens,
                "prompt_cache_enabled": JUDGE_CFG.prompt_cache_enabled,
                "temperature": 0.2 if _supports_temperature(model_name) else "N/A (不支持)",
            },
            "request_body": simulated_request_body,
            "cache_request_body": cache_request_body,
            "sample_data": rows_collected[:actual_batch_size],
        }

    print("[INFO] Batch context judge API v2 registered: /api/judge-only-excel-batch")
    print("[INFO] Prompt preview API registered: /api/preview-prompt")
