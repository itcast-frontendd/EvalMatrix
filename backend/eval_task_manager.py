# -*- coding: utf-8 -*-
"""
评测任务管理模块
- 任务状态跟踪（进度、成功/失败计数）
- 逐批结果保存到磁盘
- 结果查询和导出（JSON/CSV）
"""
import json
import os
import time
import uuid
import csv
import io
from typing import Dict, Any, Optional, List
from datetime import datetime


RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "eval_results")
os.makedirs(RESULTS_DIR, exist_ok=True)


# 内存中的任务状态（进程重启会丢失，但磁盘结果不丢）
_tasks: Dict[str, Dict[str, Any]] = {}


def create_task(file_id: str, total_rows: int, total_batches: int, config: dict) -> str:
    """创建评测任务，返回 task_id"""
    task_id = f"{file_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    task_dir = os.path.join(RESULTS_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    task_state = {
        "task_id": task_id,
        "file_id": file_id,
        "status": "running",
        "total_rows": total_rows,
        "total_batches": total_batches,
        "completed_batches": 0,
        "failed_batches": 0,
        "completed_rows": 0,
        "failed_rows": 0,
        "start_time": time.time(),
        "end_time": None,
        "config": config,
        "task_dir": task_dir,
    }

    _tasks[task_id] = task_state

    # 保存元信息到磁盘
    with open(os.path.join(task_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "task_id": task_id,
            "file_id": file_id,
            "total_rows": total_rows,
            "total_batches": total_batches,
            "config": config,
            "start_time": datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)

    print(f"[TASK] Created task {task_id}: {total_rows} rows, {total_batches} batches")
    return task_id


def save_batch_results(task_id: str, batch_idx: int, results: list):
    """逐批保存结果到磁盘（JSONL 追加写入）"""
    task = _tasks.get(task_id)
    if not task:
        return

    task_dir = task["task_dir"]

    # 追加写入 results.jsonl（每行一个结果）
    results_file = os.path.join(task_dir, "results.jsonl")
    with open(results_file, "a", encoding="utf-8") as f:
        for r in results:
            r_copy = {**r, "batch_idx": batch_idx, "saved_at": time.time()}
            f.write(json.dumps(r_copy, ensure_ascii=False) + "\n")

    # 更新计数
    success_rows = sum(1 for r in results if r.get("status") == "success")
    failed_rows = sum(1 for r in results if r.get("status") == "failed")
    is_batch_failed = failed_rows > 0 and success_rows == 0

    task["completed_batches"] += 1
    task["completed_rows"] += success_rows
    task["failed_rows"] += failed_rows
    if is_batch_failed:
        task["failed_batches"] += 1


def finish_task(task_id: str, status: str = "completed"):
    """标记任务完成"""
    task = _tasks.get(task_id)
    if not task:
        return

    task["status"] = status
    task["end_time"] = time.time()
    elapsed = task["end_time"] - task["start_time"]

    # 保存最终状态
    summary = {
        "task_id": task_id,
        "file_id": task["file_id"],
        "status": status,
        "total_rows": task["total_rows"],
        "completed_rows": task["completed_rows"],
        "failed_rows": task["failed_rows"],
        "total_batches": task["total_batches"],
        "completed_batches": task["completed_batches"],
        "failed_batches": task["failed_batches"],
        "elapsed_seconds": round(elapsed, 1),
        "start_time": datetime.fromtimestamp(task["start_time"]).isoformat(),
        "end_time": datetime.fromtimestamp(task["end_time"]).isoformat(),
    }

    task_dir = task["task_dir"]
    with open(os.path.join(task_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"[TASK] Finished {task_id}: {status}, "
          f"{task['completed_rows']} ok / {task['failed_rows']} failed, "
          f"{elapsed:.1f}s")


def get_task_progress(task_id: str) -> Optional[Dict[str, Any]]:
    """获取任务进度"""
    task = _tasks.get(task_id)
    if not task:
        # 尝试从磁盘恢复
        task_dir = os.path.join(RESULTS_DIR, task_id)
        summary_path = os.path.join(task_dir, "summary.json")
        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    elapsed = time.time() - task["start_time"]
    progress = task["completed_batches"] / max(task["total_batches"], 1)

    # 预估剩余时间
    eta = None
    if task["completed_batches"] > 0 and task["status"] == "running":
        avg_per_batch = elapsed / task["completed_batches"]
        remaining = task["total_batches"] - task["completed_batches"]
        eta = round(avg_per_batch * remaining, 0)

    return {
        "task_id": task_id,
        "file_id": task["file_id"],
        "status": task["status"],
        "progress": round(progress, 3),
        "total_rows": task["total_rows"],
        "completed_rows": task["completed_rows"],
        "failed_rows": task["failed_rows"],
        "total_batches": task["total_batches"],
        "completed_batches": task["completed_batches"],
        "failed_batches": task["failed_batches"],
        "elapsed_seconds": round(elapsed, 1),
        "eta_seconds": eta,
    }


def load_task_results(task_id: str) -> List[Dict[str, Any]]:
    """从磁盘加载完整结果"""
    task_dir = os.path.join(RESULTS_DIR, task_id)
    results_file = os.path.join(task_dir, "results.jsonl")
    if not os.path.exists(results_file):
        return []

    results = []
    with open(results_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    results.sort(key=lambda x: x.get("row", 0))
    return results


def export_results_csv(task_id: str, dimensions: list) -> str:
    """导出结果为 CSV 字符串"""
    results = load_task_results(task_id)
    if not results:
        return ""

    output = io.StringIO()
    writer = None

    for r in results:
        row_data = {
            "row": r.get("row", ""),
            "status": r.get("status", ""),
            "question": r.get("question", "")[:200],
        }

        # 添加每个模型的答案和评分
        models_asr = r.get("models_asr", {})
        for model_name, model_eval in r.get("evaluations", {}).items():
            # 添加模型 ASR 列（如果存在）
            if models_asr.get(model_name):
                row_data[f"{model_name}_asr"] = str(models_asr[model_name])[:200]
            answer = r.get("answers", {}).get(model_name, "")
            row_data[f"{model_name}_answer"] = str(answer)[:200]

            scores = model_eval.get("scores", {})
            for dim in dimensions:
                dim_name = dim["name"]
                row_data[f"{model_name}_{dim_name}"] = scores.get(dim_name, "")

            row_data[f"{model_name}_reasoning"] = model_eval.get("reasoning", "")[:300]

        if writer is None:
            writer = csv.DictWriter(output, fieldnames=list(row_data.keys()))
            writer.writeheader()

        writer.writerow(row_data)

    return output.getvalue()


def export_results_json(task_id: str) -> list:
    """导出完整 JSON 结果"""
    return load_task_results(task_id)


def list_tasks(limit: int = 20) -> list:
    """列出最近的任务"""
    # 从磁盘扫描
    tasks = []
    if os.path.exists(RESULTS_DIR):
        for name in sorted(os.listdir(RESULTS_DIR), reverse=True)[:limit]:
            task_dir = os.path.join(RESULTS_DIR, name)
            if os.path.isdir(task_dir):
                summary_path = os.path.join(task_dir, "summary.json")
                meta_path = os.path.join(task_dir, "meta.json")
                if os.path.exists(summary_path):
                    with open(summary_path, "r", encoding="utf-8") as f:
                        tasks.append(json.load(f))
                elif os.path.exists(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        meta["status"] = "running" if name in _tasks else "interrupted"
                        tasks.append(meta)
    return tasks
