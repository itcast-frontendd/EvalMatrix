# -*- coding: utf-8 -*-
"""
Judge调用器工厂函数
直接复用 main.py 中的 JUDGE_CFG 全局配置

v2: 支持 Prompt Cache (Claude 系列模型)
- 当 prompt_cache_enabled=True 且模型为 Claude 时，使用结构化 system+user 请求体
- Gemini 模型自带隐式缓存，无需特殊处理

v3: 自动限制 max_output_tokens 适配不同模型上限
"""
import asyncio
import json
import requests


# 不支持 temperature 参数的模型（o 系列推理模型）
_NO_TEMPERATURE_MODELS = {
    "o1-preview", "o1-mini", "o1",
    "o3", "o3-mini", "o3-pro",
    "o4-mini",
}


def _supports_temperature(model: str) -> bool:
    """判断模型是否支持 temperature 参数"""
    model_lower = model.lower()
    for m in _NO_TEMPERATURE_MODELS:
        if m == model_lower or model_lower.startswith(m + "-"):
            return False
    return True
_MODEL_MAX_OUTPUT_TOKENS = {
    "claude-opus-4-1": 32000,
    "claude-opus-4-1-20250805": 32000,
    "claude-sonnet-4-5": 64000,
    "claude-sonnet-4-5-20250514": 64000,
    "claude-opus-4-6": 32000,
    # 默认 Claude 系列保守值
    "_claude_default": 32000,
}


def get_safe_max_output_tokens(model: str, configured_max: int) -> int:
    """
    根据模型名称返回安全的 max_output_tokens 值。
    如果配置值超过模型上限，自动截断并打印警告。
    """
    model_lower = model.lower()

    # 精确匹配
    if model_lower in _MODEL_MAX_OUTPUT_TOKENS:
        limit = _MODEL_MAX_OUTPUT_TOKENS[model_lower]
    else:
        # 模糊匹配
        limit = None
        for key, val in _MODEL_MAX_OUTPUT_TOKENS.items():
            if key != "_claude_default" and key in model_lower:
                limit = val
                break

        # Claude 系列通用默认
        if limit is None and any(kw in model_lower for kw in ["claude", "anthropic"]):
            limit = _MODEL_MAX_OUTPUT_TOKENS["_claude_default"]

    if limit is not None and configured_max > limit:
        print(f"[WARN] max_output_tokens={configured_max} 超过模型 {model} 上限 {limit}，自动调整为 {limit}", flush=True)
        return limit

    return configured_max


def create_judge_caller(use_prompt_cache: bool = False):
    """
    创建Judge模型调用器，返回 async 函数

    Args:
        use_prompt_cache: 是否使用 prompt cache（由调用方根据配置传入）
    """
    from main import JUDGE_CFG, get_judge_config
    from prompt_cache_builder import is_claude_model

    judge_config = get_judge_config()
    if not judge_config:
        raise ValueError("Judge配置未设置，请在Settings中配置JUDGE_API_KEY")

    api_url = judge_config["url"]
    api_key = judge_config["key"]
    model = judge_config["model"]

    max_retries = JUDGE_CFG.max_retries
    base_delay = JUDGE_CFG.base_retry_delay
    timeout = JUDGE_CFG.timeout
    
    # 根据模型限制 max_output_tokens
    safe_max_tokens = get_safe_max_output_tokens(model, JUDGE_CFG.max_output_tokens)

    # 判断是否实际启用 cache（需要同时满足开关开启 + Claude 模型）
    actual_cache_enabled = use_prompt_cache and is_claude_model(model)
    if use_prompt_cache and not is_claude_model(model):
        print(f"[INFO] Prompt Cache 已开启但模型 {model} 非 Claude 系列，将忽略 cache_control")

    # 跟踪是否为当前caller的首次请求，打印完整请求内容用于调试
    _first_call_logged = {"done": False}

    async def judge_caller(prompt: str, system_parts: list = None, user_messages: list = None) -> str:
        """
        调用 Judge 模型

        Args:
            prompt: 完整 prompt 文本（非 cache 模式使用）
            system_parts: 结构化 system 分段（cache 模式使用）
            user_messages: 结构化 user messages（cache 模式使用）
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:3000",
            "X-Title": "AI Tester Batch Context Judge",
        }

        # 构建请求体
        temp_param = {"temperature": 0.2} if _supports_temperature(model) else {}
        
        if actual_cache_enabled and system_parts and user_messages:
            # Claude Prompt Cache 模式
            payload = {
                "model": model,
                "system": system_parts,
                "messages": user_messages,
                "max_tokens": safe_max_tokens,
                **temp_param,
            }
        else:
            # 普通模式
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": safe_max_tokens,
                **temp_param,
            }

        # 每个文件（每次create_judge_caller）的第一条请求打印完整内容
        if not _first_call_logged["done"]:
            _first_call_logged["done"] = True
            print("\n" + "=" * 80, flush=True)
            print("[FIRST_REQUEST_DEBUG] 当前文件/caller的首条完整请求内容：", flush=True)
            print(f"  URL: {api_url}", flush=True)
            print(f"  Model: {model}", flush=True)
            print(f"  Prompt Cache: {actual_cache_enabled}", flush=True)
            print(f"  Temperature: {'N/A (unsupported)' if not _supports_temperature(model) else 0.2}", flush=True)
            if actual_cache_enabled and system_parts:
                print(f"  System parts: {len(system_parts)}", flush=True)
                print(f"  User messages: {len(user_messages)}", flush=True)
                # 打印完整 payload 结构
                print("-" * 80, flush=True)
                print(f"  Full payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)[:5000]}", flush=True)
            else:
                print(f"  Prompt length: {len(prompt)} chars", flush=True)
                print("-" * 80, flush=True)
                print(f"  Full prompt:\n{prompt}", flush=True)
            print("=" * 80 + "\n", flush=True)

        for attempt in range(max_retries):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: requests.post(api_url, headers=headers, json=payload, timeout=timeout),
                )

                # 打印HTTP状态码，便于调试
                print(f"[DEBUG] Judge API response: HTTP {response.status_code}, "
                      f"content-length={response.headers.get('content-length', 'N/A')}", flush=True)

                # 打印 cache 使用信息（如果存在）
                if actual_cache_enabled:
                    try:
                        resp_data = response.json()
                        usage = resp_data.get("usage", {})
                        cache_creation = usage.get("cache_creation_input_tokens", 0)
                        cache_read = usage.get("cache_read_input_tokens", 0)
                        if cache_creation or cache_read:
                            print(f"[CACHE] cache_creation_tokens={cache_creation}, "
                                  f"cache_read_tokens={cache_read}", flush=True)
                    except Exception:
                        pass

                if response.status_code == 429:
                    # 429 使用更激进的退避：基础 5s，指数增长，加随机抖动
                    import random
                    rate_limit_base = max(base_delay, 5)
                    wait = rate_limit_base * (2 ** attempt) + random.uniform(0, 3)
                    # 尝试从 Retry-After 头获取建议等待时间
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = max(wait, float(retry_after))
                        except ValueError:
                            pass
                    print(f"[WARN] Judge API rate limit (429), retry in {wait:.1f}s (attempt {attempt + 1}/{max_retries})...")
                    await asyncio.sleep(wait)
                    continue

                # 非200先打印完整响应体，再 raise
                if not response.ok:
                    print(f"[ERROR] Judge API HTTP {response.status_code} raw body:\n{response.text[:2000]}", flush=True)
                    
                    # 自动处理不支持 temperature 的情况：去掉 temperature 重试
                    if response.status_code == 400 and "temperature" in response.text.lower() and "temperature" in payload:
                        print(f"[WARN] 模型 {model} 不支持 temperature 参数，自动去除后重试", flush=True)
                        del payload["temperature"]
                        continue
                    
                    response.raise_for_status()

                data = response.json()

                # 检查 choices 是否存在
                choices = data.get("choices", [])
                if not choices:
                    print(f"[ERROR] Judge API returned no choices. Full response:\n"
                          f"{json.dumps(data, ensure_ascii=False)[:2000]}", flush=True)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(base_delay * (2 ** attempt))
                        continue
                    raise ValueError(f"Judge API返回无choices: {json.dumps(data, ensure_ascii=False)[:500]}")

                content = choices[0].get("message", {}).get("content", "")

                # 检查空响应——模型可能因 prompt 冲突或 safety filter 返回空
                if not content or not content.strip():
                    finish_reason = choices[0].get("finish_reason", "unknown")
                    print(f"[WARN] Judge returned EMPTY content (finish_reason={finish_reason}), "
                          f"full response:\n{json.dumps(data, ensure_ascii=False)[:2000]}", flush=True)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(base_delay * (2 ** attempt))
                        continue
                    raise ValueError(f"Judge模型返回空内容 (finish_reason={finish_reason})")

                return content

            except requests.exceptions.Timeout:
                print(f"[WARN] Judge timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(base_delay * (2 ** attempt))
                else:
                    raise TimeoutError(f"Judge请求超时 ({timeout}s)")

            except Exception as e:
                print(f"[ERROR] Judge call failed (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(base_delay * (2 ** attempt))
                else:
                    raise

        raise Exception("Judge调用失败: 超过最大重试次数")

    return judge_caller
