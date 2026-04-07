# -*- coding: utf-8 -*-
"""
批量上下文评分模块（v2）
每次模型调用处理N条数据，确保上下文连贯性

v2 变更:
- build_batch_prompt 增加 file_context 参数（文件级上下文，如完整转录）
- build_batch_prompt 增加 context_fields 支持（行级附加上下文字段）
- 所有变更向后兼容，不传新参数时行为与v1完全一致
"""
import asyncio
import os
import traceback
from typing import List, Dict, Any, Optional
import json


# ── Prompt 模板加载 ──

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_BATCH_PROMPT_TEMPLATE: Optional[str] = None


def _load_batch_prompt_template() -> Optional[str]:
    """
    尝试加载 judge_batch_prompt.txt 作为V2批量评测的 Prompt 模板。
    【注意】只加载 judge_batch_prompt.txt，不再回退到 judge_prompt.txt，
    因为 judge_prompt.txt 的输出格式（evaluations 包裹）与 V2 批量解析器
    期望的 data_1/data_2 格式不兼容，回退会导致 "Missing evaluation in response"。
    如果 judge_batch_prompt.txt 不存在，则回退到内置默认模板。

    模板支持以下占位符:
    - {scenario}          评测场景描述
    - {file_context_block}  文件级上下文（已格式化，含标签）
    - {dimensions_text}   评分维度说明
    - {data_block}        数据内容（多条）
    - {output_format}     输出格式说明（JSON 结构示例）
    - {batch_count}       当前批次数据条数
    """
    global _BATCH_PROMPT_TEMPLATE
    if _BATCH_PROMPT_TEMPLATE is not None:
        return _BATCH_PROMPT_TEMPLATE if _BATCH_PROMPT_TEMPLATE else None

    # 只加载 V2 专用模板，不回退到 V1 的 judge_prompt.txt
    fpath = os.path.join(_BASE_DIR, "judge_batch_prompt.txt")
    if os.path.exists(fpath):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                _BATCH_PROMPT_TEMPLATE = content
                print(f"[INFO] Loaded batch prompt template from {fpath}", flush=True)
                return content
        except Exception as e:
            print(f"[WARN] Failed to read {fpath}: {e}", flush=True)

    _BATCH_PROMPT_TEMPLATE = ""
    return None


async def judge_with_batch_context(
    scenario: str,
    dimensions: List[Dict[str, Any]],
    batch_data: List[Dict[str, Any]],
    judge_caller,
    row_indices: List[int],
    file_context: str = "",
    prompt_cache_enabled: bool = False,
) -> List[Dict[str, Any]]:
    """
    批量上下文评分：每次调用处理N条数据

    Args:
        scenario: 评测场景描述
        dimensions: 评分维度列表
        batch_data: N条数据列表，每条包含:
            - "question": str  (主输入字段)
            - "models": {model_name: answer}  (待评分字段)
            - "context_fields": {field_name: value}  (可选，行级附加上下文)
        judge_caller: 异步函数，接受prompt字符串返回响应文本
        row_indices: N条数据对应的原始行索引
        file_context: 文件级上下文（如完整转录文本），所有batch共享
        prompt_cache_enabled: 是否启用 prompt cache

    Returns:
        List[Dict]: N条数据的评分结果列表
    """
    # 构建子块（无论是否使用 cache 都需要）
    prompt = build_batch_prompt(scenario, dimensions, batch_data, file_context)

    # 如果启用 prompt cache，同时构建结构化分段
    system_parts = None
    user_messages = None
    if prompt_cache_enabled:
        try:
            from prompt_cache_builder import build_cached_prompt_parts, is_claude_model
            from main import JUDGE_CFG
            if is_claude_model(JUDGE_CFG.model):
                # 需要重新构建子块用于 cache 模式
                dims_lines, data_block, file_context_block, output_format, batch_count = \
                    _build_prompt_sub_blocks(scenario, dimensions, batch_data, file_context)
                system_parts, user_messages = build_cached_prompt_parts(
                    scenario=scenario,
                    dimensions_text=dims_lines,
                    file_context_block=file_context_block,
                    data_block=data_block,
                    output_format=output_format,
                    batch_count=batch_count,
                )
                print(f"[CACHE] 构建 cache prompt 成功: system_parts={len(system_parts)}, "
                      f"user_messages={len(user_messages)}", flush=True)
            else:
                print(f"[CACHE] 模型 {JUDGE_CFG.model} 非 Claude 系列，跳过 cache 构建", flush=True)
        except Exception as e:
            print(f"[WARN] 构建 cache prompt 失败，回退到普通模式: {e}", flush=True)
            traceback.print_exc()
            system_parts = None
            user_messages = None
    else:
        print(f"[DEBUG] prompt_cache_enabled=False，使用普通模式", flush=True)

    try:
        response = await judge_caller(prompt, system_parts=system_parts, user_messages=user_messages)

        # 打印原始响应前500字符，便于调试
        print(f"[DEBUG] Batch response (rows {row_indices}): "
              f"{response[:500] if response else '(EMPTY)'}...", flush=True)

        # 防御层：如果 judge_caller 返回空/None，直接报错（不进 parse）
        if not response or not response.strip():
            raise ValueError("Judge模型返回空内容，可能是 prompt 过长或格式冲突导致")

        evaluations = parse_batch_response(response, batch_data, dimensions)

        for i, eval_result in enumerate(evaluations):
            eval_result["row"] = row_indices[i]
            eval_result["question"] = batch_data[i]["question"]
            # 将原始译文/答案写入结果，确保导出CSV时 answer 字段不为空
            eval_result["answers"] = batch_data[i]["models"]
            # 将各模型的 ASR 文本写入结果，确保导出CSV时有 ASR 列
            if batch_data[i].get("models_asr"):
                eval_result["models_asr"] = batch_data[i]["models_asr"]

        return evaluations

    except Exception as e:
        print(f"[ERROR] Batch context judge failed: {e}\n{traceback.format_exc()}")
        fallback = []
        for i in range(len(batch_data)):
            entry = {
                "row": row_indices[i],
                "question": batch_data[i]["question"],
                "evaluations": {},
                "error": str(e),
            }
            if batch_data[i].get("models_asr"):
                entry["models_asr"] = batch_data[i]["models_asr"]
            fallback.append(entry)
        return fallback


def _build_data_block(batch_data: List[Dict[str, Any]]) -> List[str]:
    """
    构建数据内容行列表（单模型/多模型兼容）

    单模型格式:
      INPUT：{question}
      {model}-ANSWER：{answer}

    多模型对齐格式（当 models_asr 存在时）:
      REFERENCE_TEXT：{question}         ← 原文 txt 内容作为参考
      {model}-ASR：{model 自己的 ASR}    ← 各模型识别的原始文本
      {model}-ANSWER：{model 的翻译}     ← 各模型的翻译输出
    """
    has_model_asr = any(data.get("models_asr") for data in batch_data)

    data_lines = []
    for idx, data in enumerate(batch_data, 1):
        data_lines.append(f"\n**DATA {idx}**")
        ctx = data.get("context_fields", {})
        if ctx:
            for field_name, field_val in ctx.items():
                if field_val:
                    data_lines.append(f"  [{field_name}]: {field_val}")

        if has_model_asr:
            # 多模型对齐格式
            data_lines.append(f"  REFERENCE_TEXT：{data['question']}\n")
            models_asr = data.get("models_asr", {})
            for model_name, answer in data["models"].items():
                model_asr = models_asr.get(model_name, "")
                if model_asr:
                    data_lines.append(f"  {model_name}-ASR：\n  {model_asr}\n")
                data_lines.append(f"  {model_name}-ANSWER：\n  {answer}\n")
        else:
            # 单模型格式（保持现有行为）
            data_lines.append(f"  INPUT：{data['question']}\n")
            for model_name, answer in data["models"].items():
                data_lines.append(f"  {model_name}-ANSWER：\n  {answer}\n")

        data_lines.append("-" * 60)
    return data_lines


def _build_prompt_sub_blocks(
    scenario: str,
    dimensions: List[Dict[str, Any]],
    batch_data: List[Dict[str, Any]],
    file_context: str = "",
):
    """
    构建 prompt 子块（供 cache 模式使用），返回各子块文本。
    与 build_batch_prompt 内的子块构建逻辑一致。
    
    Returns:
        (dimensions_text, data_block, file_context_block, output_format, batch_count)
    """
    import re as _re

    def _clean_scenario_output_instructions(text: str) -> str:
        patterns = [
            r'#\s*Output\s+Requirements.*?(?=(?:\n#\s|\Z))',
            r'#\s*Output\s+Format.*?(?=(?:\n#\s|\Z))',
            r'#\s*Content\s+Constraints.*?(?=(?:\n#\s|\Z))',
        ]
        cleaned = text
        for pat in patterns:
            cleaned = _re.sub(pat, '', cleaned, flags=_re.DOTALL | _re.IGNORECASE)
        cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        return cleaned

    scenario = _clean_scenario_output_instructions(scenario)

    # 文件级上下文块
    if file_context:
        file_context_block = (
            "**全局参考上下文**（完整原文转录，用于理解整体语境和校验翻译准确性）:\n"
            "<file_context>\n"
            f"{file_context}\n"
            "</file_context>\n"
        )
    else:
        file_context_block = ""

    # 评分维度块
    dims_lines = []
    for dim in dimensions:
        dim_type = dim.get("type", "scale")
        dim_name = dim["name"]
        dim_desc = dim["description"]
        dim_weight = dim.get("weight", 5)
        if dim_type == "scale":
            dims_lines.append(f"- {dim_name} (weight{dim_weight}): {dim_desc} [1-10 Score]")
        elif dim_type == "binary":
            dims_lines.append(f"- {dim_name} (weight{dim_weight}): {dim_desc} [true/false]")
        elif dim_type == "categorical":
            options = dim.get("options", [])
            dims_lines.append(f"- {dim_name} (weight{dim_weight}): {dim_desc} [options: {', '.join(options)}]")
    dimensions_text = "\n".join(dims_lines)

    # 数据内容块
    data_lines = _build_data_block(batch_data)
    data_block = "\n".join(data_lines)

    # JSON 输出格式示例块
    sample_model_names = list(batch_data[0]["models"].keys()) if batch_data else ["Model 1"]
    dim_names = [d["name"] for d in dimensions]
    fmt_lines = ['{\n']
    for di in range(1, len(batch_data) + 1):
        d_comma = "," if di < len(batch_data) else ""
        fmt_lines.append(f'  "data_{di}": {{\n')
        for mi, mname in enumerate(sample_model_names):
            m_comma = "," if mi < len(sample_model_names) - 1 else ""
            scores_str = ", ".join('"%s": <Score/True/False/Options>' % dn for dn in dim_names)
            fmt_lines.append('    "%s": {"scores": {%s}, "reasoning": "Brief Reasons in Simple Chinese"}%s\n' % (mname, scores_str, m_comma))
        fmt_lines.append(f'  }}{d_comma}\n')
    fmt_lines.append('}')
    output_format = "".join(fmt_lines)

    batch_count = str(len(batch_data))

    return dimensions_text, data_block, file_context_block, output_format, batch_count


def build_batch_prompt(
    scenario: str,
    dimensions: List[Dict[str, Any]],
    batch_data: List[Dict[str, Any]],
    file_context: str = "",
) -> str:
    """
    构建批量评分Prompt

    优先加载 judge_batch_prompt.txt 模板；
    模板不存在时回退到内置默认模板（不再回退 judge_prompt.txt，
    因其输出格式与 V2 解析器不兼容）。

    Args:
        scenario: 评测场景
        dimensions: 评分维度
        batch_data: N条数据
        file_context: 文件级上下文文本（可选）
    """

    # ── 0. 清理 scenario 中冲突的输出格式指令 ──
    # scenario 可能包含用户自定义的 Output Requirements / Output Format 等指令，
    # 这些指令（如要求 pipe 分隔单行输出）会与我们的 JSON 输出要求冲突，
    # 导致模型困惑返回空内容。需要剥离这些输出格式段落，只保留 Role/Task 描述。
    import re as _re

    def _clean_scenario_output_instructions(text: str) -> str:
        """移除 scenario 中的输出格式指令段落，避免与 batch JSON 输出格式冲突"""
        # 匹配常见的输出格式指令段：
        # "# Output Requirements...", "# Output Format...", "# Content Constraints..."
        # 从这些标题开始到下一个 "#" 标题或文本结束
        patterns = [
            r'#\s*Output\s+Requirements.*?(?=(?:\n#\s|\Z))',
            r'#\s*Output\s+Format.*?(?=(?:\n#\s|\Z))',
            r'#\s*Content\s+Constraints.*?(?=(?:\n#\s|\Z))',
        ]
        cleaned = text
        for pat in patterns:
            cleaned = _re.sub(pat, '', cleaned, flags=_re.DOTALL | _re.IGNORECASE)
        # 清理多余空行
        cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        return cleaned

    scenario = _clean_scenario_output_instructions(scenario)

    # ── 子块构建（无论用模板还是默认都需要） ──

    # 文件级上下文块
    if file_context:
        file_context_block = (
            "**全局参考上下文**（完整原文转录，用于理解整体语境和校验翻译准确性）:\n"
            "<file_context>\n"
            f"{file_context}\n"
            "</file_context>\n"
        )
    else:
        file_context_block = ""

    # 评分维度块
    dims_lines = []
    for dim in dimensions:
        dim_type = dim.get("type", "scale")
        dim_name = dim["name"]
        dim_desc = dim["description"]
        dim_weight = dim.get("weight", 5)

        if dim_type == "scale":
            dims_lines.append(f"- {dim_name} (weight{dim_weight}): {dim_desc} [1-10 Score]")
        elif dim_type == "binary":
            dims_lines.append(f"- {dim_name} (weight{dim_weight}): {dim_desc} [true/false]")
        elif dim_type == "categorical":
            options = dim.get("options", [])
            dims_lines.append(f"- {dim_name} (weight{dim_weight}): {dim_desc} [options: {', '.join(options)}]")
    dimensions_text = "\n".join(dims_lines)

    # 数据内容块
    data_lines = _build_data_block(batch_data)
    data_block = "\n".join(data_lines)

    # JSON 输出格式示例块
    sample_model_names = list(batch_data[0]["models"].keys()) if batch_data else ["Model 1"]
    dim_names = [d["name"] for d in dimensions]
    fmt_lines = ['{\n']
    for di in range(1, len(batch_data) + 1):
        d_comma = "," if di < len(batch_data) else ""
        fmt_lines.append(f'  "data_{di}": {{\n')
        for mi, mname in enumerate(sample_model_names):
            m_comma = "," if mi < len(sample_model_names) - 1 else ""
            scores_str = ", ".join('"%s": <Score/True/False/Options>' % dn for dn in dim_names)
            fmt_lines.append('    "%s": {"scores": {%s}, "reasoning": "Brief Reasons in Simple Chinese"}%s\n' % (mname, scores_str, m_comma))
        fmt_lines.append(f'  }}{d_comma}\n')
    fmt_lines.append('}')
    output_format = "".join(fmt_lines)

    batch_count = str(len(batch_data))

    # ── 尝试使用模板 ──
    template = _load_batch_prompt_template()
    if template:
        try:
            prompt = template.format(
                scenario=scenario,
                file_context_block=file_context_block,
                dimensions_text=dimensions_text,
                data_block=data_block,
                output_format=output_format,
                batch_count=batch_count,
            )
            return prompt
        except KeyError as e:
            print(f"[WARN] Prompt template placeholder missing: {e}, falling back to builtin", flush=True)

    # ── 内置默认模板 ──
    prompt = f"""# Role: The Ruthless Adjudicator
You are an uncompromising AI Quality Judge. Your distinct capability is "Zero-Tolerance Evaluation." You do not grade on a curve. You do not "guess" the intent of a poor translation. You meticulously analyze the alignment between Source, Context, and Target Output.
Your goal is to expose every single flaw—hallucinations, logic breaks, segmentation errors, and stylistic failures—based strictly on the provided dimensions.

# 1. Operational Context
**Scenario Description**:
{scenario}

**Global Context Reference (Ground Truth)**:
Use the following content to resolve any ambiguities in the specific data entries. If the model output contradicts this context, it is a critical failure.
{file_context_block}

# 2. The Law (Evaluation Dimensions)
You must adhere strictly to these scoring criteria. Do not invent your own rules.
{dimensions_text}

# 3. The Evidence (Data to Evaluate)
**Batch Size**: {batch_count} entries.
**Data Source**:
{data_block}

# 4. Adjudication Protocol (Step-by-Step)
For EACH entry in the data block, execute the following mental process before generating the JSON:
1.  **Context Check**: Read the `context` fields and the Global Context. Does the input ASR/Text make sense?
2.  **Error Detection**: Scrutinize the `model output`. Look for:
    *   **KERR Failures**: Wrong numbers, names, entities?
    *   **Hallucinations**: Did it invent text not present in the source?
    *   **Segmentation**: Is the sentence cut in a way that destroys logic?
3.  **Scoring**:Deduct points aggressively based on the Dimensions. A perfect score (10) requires perfection.
4.  **Reasoning**: Write the verdict in **Simplified Chinese**. Be concise but cutting. Point out the specific error (e.g., "漏译关键数据", "主语错误").

# 5. Hard Constraints & Output Format
1.  **Output Format**: You must output a **SINGLE VALID JSON OBJECT**. Use the exact structure below:
    ```json
    {output_format}
    ```
2.  **Empty Input Handling**: If a model field is empty or null, **all scores for that entry must be 0**, and reasoning must state "模型无输出".
3.  **Language**: The JSON keys must remain as defined. The values for "reasoning" MUST be in **Simplified Chinese**.
4.  **No Markdown**: Do NOT wrap the output in markdown code blocks (like ```json ... ```). Just return the raw JSON string.
5.  **Completeness**: You must evaluate exactly {batch_count} entries. Do not skip any.

# Begin Adjudication.
"""
    return prompt


def parse_batch_response(
    response_text: str,
    batch_data: List[Dict[str, Any]],
    dimensions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """解析批量评分响应（与v1完全相同，无需修改）"""

    json_text = response_text.strip()

    # 移除markdown代码块标记
    if "```" in json_text:
        import re
        match = re.search(r'```(?:json)?\s*\n(.*?)\n```', json_text, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
        else:
            json_text = json_text.replace("```json", "").replace("```", "").strip()

    # 尝试解析，带多层容错
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as original_err:
        import re
        fixed = json_text
        # 1. 移除 trailing commas
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
        # 2. 修复缺失的值（key 后面直接跟逗号或 }）→ 填 null
        fixed = re.sub(r':\s*,', ': null,', fixed)
        fixed = re.sub(r':\s*}', ': null}', fixed)
        # 2b. 修复 key 后面跟换行再跟 } 或 , 的情况
        fixed = re.sub(r':\s*\n\s*([,}\]])', r': null\1', fixed)
        # 3. 移除 JSON 中不合法的单行注释 // ...
        fixed = re.sub(r'//[^\n]*', '', fixed)
        # 4. 处理 NaN / Infinity → null
        fixed = re.sub(r'\bNaN\b', 'null', fixed)
        fixed = re.sub(r'\bInfinity\b', 'null', fixed)
        fixed = re.sub(r'-Infinity\b', 'null', fixed)
        # 5. 修复缺少逗号的情况（LLM 常见错误）
        #    情况a: }"key" → },"key"  (对象结尾后直接跟新 key)
        fixed = re.sub(r'(\})\s*(")', r'\1,\2', fixed)
        #    情况b: ]"key" → ],"key"  (数组结尾后直接跟新 key)
        fixed = re.sub(r'(\])\s*(")', r'\1,\2', fixed)
        #    情况c: 数字/字符串/true/false/null 后直接跟 "key" → 补逗号
        #    e.g. 8 "reasoning" → 8, "reasoning"
        fixed = re.sub(r'(\d)\s+(")', r'\1,\2', fixed)
        fixed = re.sub(r'(true|false|null)\s+(")', r'\1,\2', fixed)
        #    情况d: 字符串值后直接跟新 key → "value" "key" → "value","key"
        fixed = re.sub(r'("\s*)\s+("(?:[^"\\]|\\.)*"\s*:)', r'\1,\2', fixed)
        # 6. 再次清理可能产生的 trailing commas
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
        # 7. 尝试只提取最外层 { ... }（LLM 可能在 JSON 前后加了解释文字）
        brace_match = re.search(r'\{.*\}', fixed, re.DOTALL)
        if brace_match:
            fixed = brace_match.group(0)
        try:
            parsed = json.loads(fixed)
        except json.JSONDecodeError as regex_err:
            # 8. 终极回退：使用 json_repair 库进行智能修复
            #    能处理截断、缺值、未闭合括号等各种 LLM 常见畸形 JSON
            print(f"[WARN] Regex-based JSON fix failed ({regex_err}), trying json_repair library...")
            try:
                from json_repair import repair_json
                repaired_text = repair_json(json_text, return_objects=False)
                parsed = json.loads(repaired_text)
                print(f"[INFO] json_repair successfully fixed the response")
            except Exception as repair_err:
                print(f"[ERROR] json_repair also failed: {repair_err}")
                print(f"[ERROR] JSON parse failed after all fixes: {regex_err}")
                print(f"[DEBUG] Response text (first 2000 chars): {response_text[:2000]}")
                err_pos = getattr(original_err, 'pos', 0)
                start = max(0, err_pos - 60)
                end = min(len(json_text), err_pos + 60)
                print(f"[DEBUG] Original text around error pos {err_pos}: ...{json_text[start:end]}...")
                raise ValueError(f"Failed to parse JSON response: {regex_err}")

    results = []

    for i, data in enumerate(batch_data, 1):
        data_key = f"data_{i}"

        if data_key not in parsed:
            print(f"[WARN] Missing evaluation for {data_key}")
            results.append({
                "evaluations": {},
                "error": "Missing evaluation in response",
            })
            continue

        data_evals = parsed[data_key]
        evaluations = {}

        for model_name in data["models"].keys():
            if model_name not in data_evals:
                print(f"[WARN] Missing evaluation for model {model_name} in {data_key}")
                evaluations[model_name] = {
                    "scores": {dim["name"]: 0 for dim in dimensions},
                    "reasoning": "Missing evaluation",
                }
                continue

            model_eval = data_evals[model_name]
            scores = model_eval.get("scores", {})
            normalized_scores = {}

            for dim in dimensions:
                dim_name = dim["name"]
                score_value = scores.get(dim_name)

                if score_value is None:
                    normalized_scores[dim_name] = 0
                else:
                    dim_type = dim.get("type", "scale")
                    if dim_type == "scale":
                        try:
                            normalized_scores[dim_name] = max(1, min(10, float(score_value)))
                        except (ValueError, TypeError):
                            normalized_scores[dim_name] = 0
                    elif dim_type == "binary":
                        if isinstance(score_value, bool):
                            normalized_scores[dim_name] = score_value
                        else:
                            normalized_scores[dim_name] = str(score_value).lower() in ["true", "1", "yes"]
                    elif dim_type == "categorical":
                        normalized_scores[dim_name] = str(score_value)

            evaluations[model_name] = {
                "scores": normalized_scores,
                "reasoning": model_eval.get("reasoning", ""),
            }

        results.append({"evaluations": evaluations})

    return results


async def process_evaluation_with_batch_context(
    scenario: str,
    dimensions: List[Dict[str, Any]],
    all_rows_data: List[Dict[str, Any]],
    judge_caller,
    batch_size: int = 3,
    concurrency: int = 3,
    file_context: str = "",
    progress_callback=None,
    prompt_cache_enabled: bool = False,
    abort_checker=None,
) -> List[Dict[str, Any]]:
    """
    主函数：分批处理所有数据，支持逐批保存和失败隔离

    Args:
        scenario: 评测场景
        dimensions: 评分维度
        all_rows_data: 所有行数据
        judge_caller: Judge模型调用函数
        batch_size: 每批数据条数
        concurrency: 并发批次数
        file_context: 文件级上下文
        progress_callback: 进度回调函数 (completed, total, failed, current_batch_info)
        prompt_cache_enabled: 是否启用 prompt cache

    Returns:
        List[Dict]: 所有行的评分结果（含成功和失败标记）
    """
    batches = []
    for i in range(0, len(all_rows_data), batch_size):
        batches.append(all_rows_data[i:i + batch_size])

    total_batches = len(batches)
    print(f"[INFO] Total {len(all_rows_data)} rows, split into {total_batches} batches "
          f"(batch_size={batch_size}, file_context={len(file_context)} chars)")

    sem = asyncio.Semaphore(concurrency)
    all_results = []
    completed_count = 0
    failed_count = 0
    lock = asyncio.Lock()

    async def process_batch_with_sem(batch_idx: int, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        nonlocal completed_count, failed_count
        
        # 中断检查
        if abort_checker and abort_checker():
            row_indices = [row["row_index"] for row in batch]
            print(f"[INFO] Batch {batch_idx} skipped (aborted)", flush=True)
            fallback = []
            for i, row in enumerate(batch):
                empty_evals = {}
                for model_name in row["models"]:
                    empty_scores = {dim["name"]: 0 for dim in dimensions}
                    empty_evals[model_name] = {"scores": empty_scores, "reasoning": "[用户中断评测]"}
                entry = {
                    "row": row_indices[i], "question": row["question"],
                    "answers": row["models"], "evaluations": empty_evals,
                    "status": "aborted", "error": "Evaluation aborted by user",
                }
                if row.get("models_asr"):
                    entry["models_asr"] = row["models_asr"]
                fallback.append(entry)
            return fallback
        
        async with sem:
            row_indices = [row["row_index"] for row in batch]
            batch_data = [
                {
                    "question": row["question"],
                    "models": row["models"],
                    "context_fields": row.get("context_fields", {}),
                    "models_asr": row.get("models_asr", {}),
                }
                for row in batch
            ]

            try:
                batch_results = await judge_with_batch_context(
                    scenario, dimensions, batch_data, judge_caller, row_indices,
                    file_context=file_context,
                    prompt_cache_enabled=prompt_cache_enabled,
                )
                # 标记成功
                for r in batch_results:
                    r["status"] = "success"

                async with lock:
                    completed_count += 1
                    if progress_callback:
                        await progress_callback(completed_count, total_batches, failed_count,
                                                {"batch_idx": batch_idx, "rows": row_indices, "status": "success"})

                return batch_results

            except Exception as e:
                print(f"[ERROR] Batch {batch_idx} failed (rows {row_indices}): {e}")
                # 生成失败占位结果——保证每行都有输出
                fallback_results = []
                for i, row in enumerate(batch):
                    empty_evals = {}
                    for model_name in row["models"]:
                        empty_scores = {}
                        for dim in dimensions:
                            dim_type = dim.get("type", "scale")
                            if dim_type == "binary":
                                empty_scores[dim["name"]] = False
                            else:
                                empty_scores[dim["name"]] = 0
                        empty_evals[model_name] = {
                            "scores": empty_scores,
                            "reasoning": f"[评测失败: {str(e)[:100]}]",
                        }
                    entry = {
                        "row": row_indices[i],
                        "question": row["question"],
                        "answers": row["models"],
                        "evaluations": empty_evals,
                        "status": "failed",
                        "error": str(e)[:200],
                    }
                    if row.get("models_asr"):
                        entry["models_asr"] = row["models_asr"]
                    fallback_results.append(entry)

                async with lock:
                    completed_count += 1
                    failed_count += 1
                    if progress_callback:
                        await progress_callback(completed_count, total_batches, failed_count,
                                                {"batch_idx": batch_idx, "rows": row_indices, "status": "failed", "error": str(e)[:100]})

                return fallback_results

    tasks = [process_batch_with_sem(i, batch) for i, batch in enumerate(batches)]
    batch_results = await asyncio.gather(*tasks)

    for batch_result in batch_results:
        all_results.extend(batch_result)

    all_results.sort(key=lambda x: x["row"])

    success_rows = sum(1 for r in all_results if r.get("status") == "success")
    failed_rows = sum(1 for r in all_results if r.get("status") == "failed")
    print(f"[INFO] Batch context evaluation completed: {len(all_results)} rows "
          f"(success={success_rows}, failed={failed_rows})")
    return all_results
