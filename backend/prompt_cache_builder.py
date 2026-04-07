# -*- coding: utf-8 -*-
"""
Prompt Cache 构造模块

将 Judge 评测 prompt 拆分为结构化的 system/user 分段，
并在指定位置插入 cache_control 断点，实现 Claude Prompt Cache。

缓存断点策略：
- 缓存断点1：system[0] — 静态评分指令 + dimensions_text（可重用指令缓存）
- 缓存断点2：system[1] — file_context_block（上下文缓存）
- 缓存断点3：user messages 最后一个 content block — data_block（对话历史缓存）
"""
import json
import os
from typing import List, Dict, Any, Optional, Tuple


_CACHE_CONTROL_EPHEMERAL = {"type": "ephemeral"}


def is_claude_model(model: str) -> bool:
    """判断是否为 Claude 系列模型"""
    model_lower = model.lower()
    return any(kw in model_lower for kw in ["claude", "anthropic"])


def build_cached_prompt_parts(
    scenario: str,
    dimensions_text: str,
    file_context_block: str,
    data_block: str,
    output_format: str,
    batch_count: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    构建带 cache_control 的结构化 prompt

    Returns:
        (system_parts, user_messages)
        - system_parts: 用于 API 请求的 "system" 字段
        - user_messages: 用于 API 请求的 "messages" 字段
    """

    # ── System Part 1: 静态评分指令 + 维度定义（缓存断点1）──
    system_instructions = f"""# Role: The Ruthless Adjudicator
You are an uncompromising AI Quality Judge. Your distinct capability is "Zero-Tolerance Evaluation." You do not grade on a curve. You do not "guess" the intent of a poor translation. You meticulously analyze the alignment between Source, Context, and Target Output.
Your goal is to expose every single flaw—hallucinations, logic breaks, segmentation errors, and stylistic failures—based strictly on the provided dimensions.

# 1. Operational Context
**Scenario Description**:
{scenario}

# 2. The Law (Evaluation Dimensions)
You must adhere strictly to these scoring criteria. Do not invent your own rules.
{dimensions_text}

# 4. Adjudication Protocol (Step-by-Step)
For EACH entry in the data block, execute the following mental process before generating the JSON:
1.  **Context Check**: Read the `context` fields and the Global Context. Does the input ASR/Text make sense?
2.  **Error Detection**: Scrutinize the `model output`. Look for:
    *   **KERR Failures**: Wrong numbers, names, entities?
    *   **Hallucinations**: Did it invent text not present in the source?
    *   **Segmentation**: Is the sentence cut in a way that destroys logic?
3.  **Scoring**: Deduct points aggressively based on the Dimensions. A perfect score (10) requires perfection.
4.  **Reasoning**: Write the verdict in **Simplified Chinese**. Be concise but cutting. Point out the specific error (e.g., "漏译关键数据", "主语错误").

# 5. Hard Constraints & Output Format
1.  **Output Format**: You must output a **SINGLE VALID JSON OBJECT**. Use the exact structure below:
```json
{output_format}
```
2.  **Empty Input Handling**: If a model field is empty or null, **all scores for that entry must be 0**, and reasoning must state "模型无输出".
3.  **Language**: The JSON keys must remain as defined. The values for "reasoning" MUST be in **Simplified Chinese**.
4.  **No Markdown**: Do NOT wrap the output in markdown code blocks (like ```json ... ```). Just return the raw JSON string.
5.  **Completeness**: You must evaluate exactly {batch_count} entries. Do not skip any."""

    system_part_1 = {
        "type": "text",
        "text": system_instructions,
        "cache_control": _CACHE_CONTROL_EPHEMERAL,  # 🚩 缓存断点1
    }

    system_parts = [system_part_1]

    # ── System Part 2: 文件级上下文（缓存断点2）──
    if file_context_block and file_context_block.strip():
        system_part_2 = {
            "type": "text",
            "text": f"""# 3. Global Context Reference (Ground Truth)
Use the following content to resolve any ambiguities in the specific data entries. If the model output contradicts this context, it is a critical failure.
{file_context_block}""",
            "cache_control": _CACHE_CONTROL_EPHEMERAL,  # 🚩 缓存断点2
        }
        system_parts.append(system_part_2)

    # ── User Message: 数据块（缓存断点3）──
    user_content = [
        {
            "type": "text",
            "text": f"""# The Evidence (Data to Evaluate)
**Batch Size**: {batch_count} entries.
**Data Source**:
{data_block}

# Begin Adjudication.""",
            "cache_control": _CACHE_CONTROL_EPHEMERAL,  # 🚩 缓存断点3
        }
    ]

    user_messages = [
        {
            "role": "user",
            "content": user_content,
        }
    ]

    return system_parts, user_messages


def build_cached_single_prompt_parts(
    scenario: str,
    dimensions_text: str,
    products_text: str,
    question: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    为单条评测（非 batch context 模式）构建带 cache_control 的 prompt

    Returns:
        (system_parts, user_messages)
    """
    question_section = f"User Question/Input: {question}" if question else ""

    # ── System: 静态指令 + 维度（缓存断点1）──
    system_text = f"""You are a ruthlessly brutal AI Judge—merciless in your scrutiny, unforgiving of even the slightest error, omission, vagueness, or redundancy.

Given the following user scenario for an AI product:
"{scenario}"

Evaluation Dimensions:
{dimensions_text}

Task:
Evaluate each product's answer based on the dimensions provided.

For each dimension, output a value based on its 'type':
- If type is 'scale': Assign an integer score (1-10).
- If type is 'binary': Assign a boolean (true/false).
- If type is 'categorical': Select ONE string from the provided 'options'.

Also provide a brief reasoning for the overall evaluation.

Return the result ONLY as a valid JSON object with the following structure:
{{
    "evaluations": {{
        "EXACT_PRODUCT_ID_FROM_INPUT": {{
            "scores": {{ "Dimension Name": value, ... }},
            "reasoning": "Brief explanation...（此处需要使用中文）"
        }},
        ...
    }}
}}
Important:
1. The keys in "evaluations" MUST be the exact Product IDs strings provided in the input.
2. "scores" values must match the dimension type (int, bool, or string).
3. Output strictly valid JSON."""

    system_parts = [
        {
            "type": "text",
            "text": system_text,
            "cache_control": _CACHE_CONTROL_EPHEMERAL,  # 🚩 缓存断点1
        }
    ]

    # ── User Message: 问题 + 回答（缓存断点2）──
    user_text = f"""{question_section}

Product Answers:
{products_text}"""

    user_messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": user_text,
                    "cache_control": _CACHE_CONTROL_EPHEMERAL,  # 🚩 缓存断点2
                }
            ],
        }
    ]

    return system_parts, user_messages
