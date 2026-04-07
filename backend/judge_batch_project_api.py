# -*- coding: utf-8 -*-
"""
批量项目评测API (Batch Project)
- SSE 流式进度推送
- 逐批结果保存到磁盘（eval_results/）
- 失败隔离：单批失败不影响其他批次
- 结果导出：CSV/JSON 下载
"""
import json
import io
import os
import time
import zipfile
import traceback
import asyncio
import csv
from typing import Optional
from fastapi import UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
import pandas as pd


RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "eval_results")
os.makedirs(RESULTS_DIR, exist_ok=True)


# ════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════

def flatten_nested_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
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


def parse_jsonl_to_df(text: str) -> pd.DataFrame:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    records = []
    for line in lines:
        try:
            record = json.loads(line)
            flattened = flatten_nested_dict(record)
            records.append(flattened)
        except json.JSONDecodeError:
            pass
    if not records:
        raise ValueError("JSONL 中没有有效数据")
    return pd.DataFrame(records)


def prepare_rows_from_df(df: pd.DataFrame, col_map: dict) -> list:
    question_col = col_map["question"]
    model_cols = col_map.get("models", [])
    context_field_cols = col_map.get("context_fields", [])
    valid_context_cols = [c for c in context_field_cols if c in df.columns]

    # 多模型对齐：每个 model 可有对应的 ASR 字段 (model_asr_fields)
    model_asr_map = col_map.get("model_asr_fields", {})  # e.g. {"translation_e2e": "asr_e2e"}

    all_rows = []
    for idx, row in df.iterrows():
        raw_text = str(row[question_col])
        if pd.isna(raw_text) or not raw_text.strip() or raw_text.strip() == "nan":
            continue

        models_data = {}
        for m_col in model_cols:
            answer = row.get(m_col, "No Answer")
            if pd.isna(answer):
                answer = "No Answer"
            else:
                answer = str(answer).strip()
                if answer == "nan":
                    answer = "No Answer"
            models_data[m_col] = answer

        # 提取各模型的 ASR 文本（多模型对齐场景）
        models_asr = {}
        for m_col, asr_col in model_asr_map.items():
            if asr_col in df.columns:
                asr_val = row.get(asr_col, "")
                if pd.notna(asr_val):
                    asr_str = str(asr_val).strip()
                    if asr_str and asr_str != "nan":
                        models_asr[m_col] = asr_str

        context_fields = {}
        for c_col in valid_context_cols:
            val = row.get(c_col, "")
            if pd.notna(val):
                val_str = str(val).strip()
                if val_str and val_str != "nan":
                    context_fields[c_col] = val_str

        row_data = {
            "row_index": idx,
            "question": raw_text.strip(),
            "models": models_data,
            "context_fields": context_fields,
        }
        if models_asr:
            row_data["models_asr"] = models_asr

        all_rows.append(row_data)
    return all_rows


# ════════════════════════════════════════════════════════
# 磁盘保存
# ════════════════════════════════════════════════════════

def save_file_results(run_id: str, file_id: str, results: list, config: dict):
    """保存单个文件的评测结果"""
    run_dir = os.path.join(RESULTS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)

    # JSONL 逐行保存
    out_path = os.path.join(run_dir, f"{file_id}_results.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[SAVE] {file_id}: {len(results)} rows → {out_path}")


def save_run_summary(run_id: str, summary: dict):
    """保存整次运行汇总"""
    run_dir = os.path.join(RESULTS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def export_results_to_csv(run_id: str) -> str:
    """将运行结果导出为 CSV"""
    run_dir = os.path.join(RESULTS_DIR, run_id)
    if not os.path.exists(run_dir):
        return ""

    csv_path = os.path.join(run_dir, "results_export.csv")
    all_rows = []

    # 读取所有 *_results.jsonl 和 results.jsonl
    for fname in sorted(os.listdir(run_dir)):
        if fname.endswith("_results.jsonl") or fname == "results.jsonl":
            file_id = fname.replace("_results.jsonl", "") if fname != "results.jsonl" else ""
            with open(os.path.join(run_dir, fname), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        obj = json.loads(line)
                        obj["_file_id"] = file_id
                        all_rows.append(obj)

    if not all_rows:
        return ""

    # 构建 CSV
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = None
        for r in all_rows:
            row_data = {
                "file_id": r.get("_file_id", ""),
                "row": r.get("row", ""),
                "status": r.get("status", ""),
                "question": str(r.get("question", ""))[:500],
            }
            models_asr = r.get("models_asr", {})
            for model_name, model_eval in r.get("evaluations", {}).items():
                # 添加模型 ASR 列（如果存在）
                if models_asr.get(model_name):
                    row_data[f"{model_name}_asr"] = str(models_asr[model_name])[:500]
                row_data[f"{model_name}_answer"] = str(r.get("answers", {}).get(model_name, ""))[:500]
                for dim_name, score in model_eval.get("scores", {}).items():
                    row_data[f"{model_name}_{dim_name}"] = score
                row_data[f"{model_name}_reasoning"] = model_eval.get("reasoning", "")[:500]

            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(row_data.keys()))
                writer.writeheader()
            writer.writerow(row_data)

    print(f"[EXPORT] CSV: {csv_path} ({len(all_rows)} rows)")
    return csv_path


# ════════════════════════════════════════════════════════
# SSE 流式评测 API
# ════════════════════════════════════════════════════════

async def judge_batch_project_stream(
    file: UploadFile = File(...),
    override_scenario: Optional[str] = Form(None),
    override_dimensions: Optional[str] = Form(None),
    override_mapping: Optional[str] = Form(None),
    override_batch_size: Optional[int] = Form(None),
    override_concurrency: Optional[int] = Form(None),
    selected_files: Optional[str] = Form(None),
):
    """SSE 流式批量评测"""

    # ── 1. 解析 ZIP ──
    try:
        contents = await file.read()
        zf = zipfile.ZipFile(io.BytesIO(contents))
        names = zf.namelist()
    except Exception as e:
        async def err():
            yield f"event: error\ndata: {json.dumps({'error': f'ZIP解析失败: {e}'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    # ── 2. 读取 manifest ──
    manifest_candidates = [n for n in names if n.endswith("manifest.json")]
    if not manifest_candidates:
        async def err():
            yield f"event: error\ndata: {json.dumps({'error': 'ZIP中未找到manifest.json'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    try:
        manifest = json.loads(zf.read(manifest_candidates[0]).decode("utf-8"))
        eval_config = manifest.get("eval_config", {})
        file_entries = manifest.get("files", [])
    except Exception as e:
        async def err():
            yield f"event: error\ndata: {json.dumps({'error': f'manifest解析失败: {e}'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    # ── 3. 配置 (override > manifest) ──
    col_map = json.loads(override_mapping) if override_mapping else eval_config.get("mapping", {})
    scenario = override_scenario or eval_config.get("default_scenario", "")
    dims = json.loads(override_dimensions) if override_dimensions else eval_config.get("default_dimensions", [])
    batch_size = override_batch_size or eval_config.get("recommended_batch_size", 3)
    concurrency = override_concurrency or eval_config.get("recommended_concurrency", 3)

    if not col_map.get("question") or not col_map.get("models"):
        async def err():
            yield f"event: error\ndata: {json.dumps({'error': 'mapping缺少question或models'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    # ── 4. 筛选文件 ──
    selected_ids = set(json.loads(selected_files)) if selected_files else None

    files_to_eval = []
    for entry in file_entries:
        if selected_ids and entry["file_id"] not in selected_ids:
            continue
        matches = [n for n in names if n.endswith(entry["jsonl_file"])]
        if matches:
            files_to_eval.append({**entry, "zip_path": matches[0]})

    if not files_to_eval:
        async def err():
            yield f"event: error\ndata: {json.dumps({'error': '没有可评测的文件'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{len(files_to_eval)}f"

    # ── 5. SSE 流 ──
    async def stream():
        from judge_batch_context import process_evaluation_with_batch_context
        from judge_caller_factory import create_judge_caller
        from judge_batch_context_api import aggregate_results
        from main import JUDGE_CFG

        # 根据配置决定是否启用 prompt cache
        use_cache = JUDGE_CFG.prompt_cache_enabled
        print(f"[BATCH_PROJECT] prompt_cache_enabled={use_cache}, model={JUDGE_CFG.model}", flush=True)
        judge_caller = create_judge_caller(use_prompt_cache=use_cache)

        all_file_results = []
        total_rows_all = sum(e.get("row_count", 0) for e in files_to_eval)
        rows_done = 0

        yield sse("init", {
            "run_id": run_id,
            "total_files": len(files_to_eval),
            "total_rows": total_rows_all,
        })

        for fi, entry in enumerate(files_to_eval):
            fid = entry["file_id"]
            use_fc = entry.get("use_file_context", True)
            fc = entry.get("origin_input", "") if use_fc else ""
            row_count = entry.get("row_count", 0)

            yield sse("file_start", {"file_id": fid, "file_idx": fi, "row_count": row_count})

            try:
                jsonl_text = zf.read(entry["zip_path"]).decode("utf-8")
                df = parse_jsonl_to_df(jsonl_text)
                df.columns = [str(c).strip() for c in df.columns]

                required = [col_map["question"]] + col_map["models"]
                missing = [c for c in required if c not in df.columns]
                if missing:
                    raise ValueError(f"缺少列: {missing}")

                all_rows_data = prepare_rows_from_df(df, col_map)

                # ── 打印每个文件第一条数据的完整内容，便于确认传参 ──
                if all_rows_data:
                    first_row = all_rows_data[0]
                    print("\n" + "=" * 80, flush=True)
                    print(f"[FILE_FIRST_ROW_DEBUG] 文件 {fid} 第一条数据完整内容：", flush=True)
                    print(f"  row_index: {first_row['row_index']}", flush=True)
                    print(f"  question: {first_row['question'][:500]}", flush=True)
                    for m_name, m_answer in first_row["models"].items():
                        print(f"  模型字段 [{m_name}]: {str(m_answer)[:500]}", flush=True)
                    for c_name, c_val in first_row.get("context_fields", {}).items():
                        print(f"  上下文字段 [{c_name}]: {str(c_val)[:300]}", flush=True)
                    print(f"  file_context (前300字): {fc[:300] if fc else '(无)'}", flush=True)
                    print(f"  use_file_context: {use_fc}", flush=True)
                    print(f"  scenario: {scenario[:200]}", flush=True)
                    print(f"  dimensions: {[d['name'] for d in dims]}", flush=True)
                    print(f"  batch_size: {batch_size}, concurrency: {concurrency}", flush=True)
                    print("=" * 80 + "\n", flush=True)
                if not all_rows_data:
                    raise ValueError("没有有效数据行")

                file_batches = (len(all_rows_data) + batch_size - 1) // batch_size

                # 进度状态（用于异步回调 → 主循环 yield）
                progress_queue = asyncio.Queue()

                async def on_progress(completed, total, failed, info):
                    await progress_queue.put({
                        "file_id": fid, "file_idx": fi,
                        "batch_completed": completed, "batch_total": total,
                        "batch_failed": failed,
                    })

                # 在后台 task 中运行评测
                eval_task = asyncio.create_task(
                    process_evaluation_with_batch_context(
                        scenario=scenario, dimensions=dims,
                        all_rows_data=all_rows_data, judge_caller=judge_caller,
                        batch_size=batch_size, concurrency=concurrency,
                        file_context=fc, progress_callback=on_progress,
                        prompt_cache_enabled=use_cache,
                    )
                )

                # 消费进度事件直到评测完成
                while not eval_task.done():
                    try:
                        prog = await asyncio.wait_for(progress_queue.get(), timeout=2.0)
                        est_rows = rows_done + prog["batch_completed"] * batch_size
                        prog["rows_done_est"] = min(est_rows, total_rows_all)
                        prog["percent"] = round(min(est_rows, total_rows_all) / max(total_rows_all, 1) * 100, 1)
                        yield sse("progress", prog)
                    except asyncio.TimeoutError:
                        # 发心跳保持连接
                        yield ": heartbeat\n\n"

                # 获取结果
                results = await eval_task

                # 消费剩余进度
                while not progress_queue.empty():
                    prog = await progress_queue.get()
                    est_rows = rows_done + prog["batch_completed"] * batch_size
                    prog["rows_done_est"] = min(est_rows, total_rows_all)
                    prog["percent"] = round(min(est_rows, total_rows_all) / max(total_rows_all, 1) * 100, 1)
                    yield sse("progress", prog)

                # 保存到磁盘
                save_file_results(run_id, fid, results, {"scenario": scenario[:200], "dims": [d["name"] for d in dims]})

                # 聚合
                aggregated = aggregate_results(results, col_map["models"], dims)
                ok = sum(1 for r in results if r.get("status") == "success")
                fail = sum(1 for r in results if r.get("status") == "failed")
                rows_done += len(all_rows_data)

                fr = {"file_id": fid, "status": "success", "row_count": len(all_rows_data),
                      "success_rows": ok, "failed_rows": fail, "results": aggregated}
                all_file_results.append(fr)

                yield sse("file_done", {
                    "file_id": fid, "status": "success",
                    "row_count": len(all_rows_data), "success_rows": ok, "failed_rows": fail,
                    "percent": round(rows_done / max(total_rows_all, 1) * 100, 1),
                    "results": aggregated,
                })

            except Exception as e:
                print(f"[ERROR] {fid}: {e}\n{traceback.format_exc()}")
                rows_done += row_count
                all_file_results.append({"file_id": fid, "status": "failed", "error": str(e)})
                yield sse("file_done", {
                    "file_id": fid, "status": "failed", "error": str(e)[:200],
                    "percent": round(rows_done / max(total_rows_all, 1) * 100, 1),
                })

        zf.close()

        # 汇总
        ok_files = sum(1 for r in all_file_results if r["status"] == "success")
        total_ok_rows = sum(r.get("row_count", 0) for r in all_file_results if r["status"] == "success")

        summary = {
            "run_id": run_id,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "project_summary": {
                "total_files": len(all_file_results), "success_files": ok_files,
                "failed_files": len(all_file_results) - ok_files, "total_rows_evaluated": total_ok_rows,
            },
            "eval_config_used": {
                "scenario": scenario[:200], "mapping": col_map,
                "batch_size": batch_size, "concurrency": concurrency,
                "dimensions": [d["name"] for d in dims],
            },
            "dimensions_full": dims,
            "file_results": all_file_results,
        }
        save_run_summary(run_id, summary)
        csv_path = export_results_to_csv(run_id)

        yield sse("done", {
            "run_id": run_id,
            "summary": summary["project_summary"],
            "csv_available": bool(csv_path),
        })

    return StreamingResponse(stream(), media_type="text/event-stream")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ════════════════════════════════════════════════════════
# 路由注册
# ════════════════════════════════════════════════════════

def register_batch_project_routes(app):

    @app.post("/api/judge-batch-project")
    async def api_judge_batch_project(
        file: UploadFile = File(...),
        override_scenario: Optional[str] = Form(None),
        override_dimensions: Optional[str] = Form(None),
        override_mapping: Optional[str] = Form(None),
        override_batch_size: Optional[int] = Form(None),
        override_concurrency: Optional[int] = Form(None),
        selected_files: Optional[str] = Form(None),
    ):
        return await judge_batch_project_stream(
            file, override_scenario, override_dimensions,
            override_mapping, override_batch_size, override_concurrency,
            selected_files,
        )

    @app.post("/api/parse-manifest")
    async def api_parse_manifest(file: UploadFile = File(...)):
        try:
            contents = await file.read()
            zf = zipfile.ZipFile(io.BytesIO(contents))
            names = zf.namelist()
            mc = [n for n in names if n.endswith("manifest.json")]
            if not mc:
                return {"error": "ZIP 中未找到 manifest.json", "status": "failed"}
            manifest = json.loads(zf.read(mc[0]).decode("utf-8"))
            ec = manifest.get("eval_config", {})
            fe = manifest.get("files", [])
            fp = []
            for entry in fe:
                jn = entry.get("jsonl_file", "")
                exists = any(n.endswith(jn) for n in names)
                fp.append({
                    "file_id": entry.get("file_id", "unknown"),
                    "jsonl_file": jn,
                    "row_count": entry.get("row_count", 0),
                    "origin_input_chars": entry.get("origin_input_chars", 0),
                    "use_file_context": entry.get("use_file_context", True),
                    "context_window": entry.get("context_window", 2),
                    "exists_in_zip": exists,
                })
            zf.close()
            return {"status": "success", "manifest": {"version": manifest.get("version", ""), "description": manifest.get("description", ""), "total_rows": manifest.get("total_rows", 0)}, "eval_config": ec, "files": fp, "zip_contents": names}
        except Exception as e:
            return {"error": f"解析失败: {str(e)}", "status": "failed"}

    @app.get("/api/eval-results/{run_id}/csv")
    async def api_download_csv(run_id: str):
        csv_path = os.path.join(RESULTS_DIR, run_id, "results_export.csv")
        if not os.path.exists(csv_path):
            csv_path = export_results_to_csv(run_id)
        if not csv_path or not os.path.exists(csv_path):
            return {"error": "结果文件不存在"}
        return FileResponse(csv_path, filename=f"{run_id}_results.csv", media_type="text/csv")

    @app.get("/api/eval-results/{run_id}/json")
    async def api_download_json(run_id: str):
        sp = os.path.join(RESULTS_DIR, run_id, "summary.json")
        if not os.path.exists(sp):
            return {"error": "结果文件不存在"}
        return FileResponse(sp, filename=f"{run_id}_summary.json", media_type="application/json")

    @app.get("/api/eval-results")
    async def api_list_results():
        runs = []
        if os.path.exists(RESULTS_DIR):
            for d in sorted(os.listdir(RESULTS_DIR), reverse=True)[:50]:
                sp = os.path.join(RESULTS_DIR, d, "summary.json")
                if os.path.exists(sp):
                    with open(sp, "r", encoding="utf-8") as f:
                        s = json.load(f)
                    runs.append({"run_id": d, "timestamp": s.get("timestamp", ""), "summary": s.get("project_summary", {})})
        return {"runs": runs}

    print("[INFO] Batch project API registered: /api/judge-batch-project, /api/parse-manifest, /api/eval-results/*")

    # ── ZIP Prompt Preview ──
    @app.post("/api/preview-prompt-zip")
    async def api_preview_prompt_zip(
        file: UploadFile = File(...),
        scenario: str = Form(""),
        dimensions: str = Form("[]"),
        preview_file_id: Optional[str] = Form(None),
        preview_rows: int = Form(2),
    ):
        """
        从 ZIP 中读取指定文件的前 N 行数据，构建实际 prompt 预览。
        """
        try:
            contents = await file.read()
            zf = zipfile.ZipFile(io.BytesIO(contents))
            names = zf.namelist()
        except Exception as e:
            return {"error": f"ZIP解析失败: {e}", "status": "failed"}

        # 读取 manifest
        manifest_candidates = [n for n in names if n.endswith("manifest.json")]
        if not manifest_candidates:
            return {"error": "ZIP中未找到manifest.json", "status": "failed"}

        try:
            manifest = json.loads(zf.read(manifest_candidates[0]).decode("utf-8"))
            eval_config = manifest.get("eval_config", {})
            file_entries = manifest.get("files", [])
        except Exception as e:
            return {"error": f"manifest解析失败: {e}", "status": "failed"}

        # 配置
        col_map = eval_config.get("mapping", {})
        effective_scenario = scenario or eval_config.get("default_scenario", "")
        try:
            dims = json.loads(dimensions) if dimensions and dimensions != "[]" else eval_config.get("default_dimensions", [])
        except:
            dims = eval_config.get("default_dimensions", [])
        batch_size = eval_config.get("recommended_batch_size", 3)

        if not col_map.get("question") or not col_map.get("models"):
            return {"error": "manifest中mapping缺少question或models字段", "status": "failed"}

        # 选择要预览的文件
        target_entry = None
        for entry in file_entries:
            matches = [n for n in names if n.endswith(entry.get("jsonl_file", ""))]
            if not matches:
                continue
            entry["zip_path"] = matches[0]
            if preview_file_id and entry.get("file_id") == preview_file_id:
                target_entry = entry
                break
            if target_entry is None:
                target_entry = entry  # 默认第一个可用文件

        if not target_entry:
            return {"error": "ZIP中没有可预览的文件", "status": "failed"}

        # 读取文件数据
        try:
            jsonl_text = zf.read(target_entry["zip_path"]).decode("utf-8")
            df = parse_jsonl_to_df(jsonl_text)
            df.columns = [str(c).strip() for c in df.columns]
        except Exception as e:
            return {"error": f"读取文件失败: {e}", "status": "failed"}

        zf.close()

        # 准备数据行
        all_rows_data = prepare_rows_from_df(df, col_map)
        if not all_rows_data:
            return {"error": "没有有效数据行", "status": "failed"}

        preview_data = all_rows_data[:min(preview_rows, len(all_rows_data))]

        # 获取 file_context
        use_fc = target_entry.get("use_file_context", True)
        fc = target_entry.get("origin_input", "") if use_fc else ""

        # 构建 prompt
        try:
            from judge_batch_context import build_batch_prompt, _load_batch_prompt_template
            from main import JUDGE_CFG, get_judge_config

            batch_data = [
                {
                    "question": row["question"],
                    "models": row["models"],
                    "context_fields": row.get("context_fields", {}),
                    **({"models_asr": row["models_asr"]} if "models_asr" in row else {}),
                }
                for row in preview_data
            ]

            actual_batch_size = min(batch_size, len(batch_data))
            first_batch = batch_data[:actual_batch_size]

            prompt = build_batch_prompt(
                scenario=effective_scenario,
                dimensions=dims,
                batch_data=first_batch,
                file_context=fc,
            )

            # 模型配置
            judge_config = get_judge_config()
            template_obj = _load_batch_prompt_template()
            template_source = "file (judge_batch_prompt.txt)" if template_obj else "builtin (default)"

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

            # Cache request body
            cache_request_body = None
            if JUDGE_CFG.prompt_cache_enabled:
                try:
                    from prompt_cache_builder import build_cached_prompt_parts, is_claude_model
                    if is_claude_model(model_name):
                        from judge_batch_context import _build_prompt_sub_blocks
                        dims_text, data_block, fc_block, output_fmt, bc = \
                            _build_prompt_sub_blocks(effective_scenario, dims, first_batch, fc)
                        system_parts, user_messages = build_cached_prompt_parts(
                            scenario=effective_scenario,
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
                "preview_file_id": target_entry.get("file_id", "unknown"),
                "file_context_chars": len(fc),
                "api_version": "v2",
                "model_config": {
                    "model": model_name,
                    "api_url": judge_config["url"] if judge_config else "(未配置)",
                    "max_output_tokens": safe_max_tokens,
                    "prompt_cache_enabled": JUDGE_CFG.prompt_cache_enabled,
                    "temperature": 0.2 if _supports_temperature(model_name) else "N/A (不支持)",
                },
                "request_body": simulated_request_body,
                "cache_request_body": cache_request_body,
                "sample_data": preview_data[:actual_batch_size],
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"Prompt构建失败: {str(e)}", "status": "failed"}

    print("[INFO] ZIP Prompt preview API registered: /api/preview-prompt-zip")
