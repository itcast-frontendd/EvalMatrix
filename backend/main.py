# -*- coding: utf-8 -*-
import os
import sys

# ── 解决双模块问题 ──
# 当以 `backend.main` 启动 (uvicorn backend.main:app) 时，
# 子模块 `from main import JUDGE_CFG` 会创建第二个模块实例。
# 通过在 sys.modules 中注册别名，确保 `import main` 和 `import backend.main`
# 返回同一个模块对象。
_this_module = sys.modules[__name__]
if __name__ != "main" and "main" not in sys.modules:
    sys.modules["main"] = _this_module
elif __name__ == "main" and "backend.main" not in sys.modules:
    sys.modules["backend.main"] = _this_module

import json
import base64
import requests
import re
import asyncio
import time
import traceback
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import pandas as pd
import io
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from sklearn.decomposition import PCA
from datetime import datetime
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Ensure backend modules can be imported (e.g. judge_batch_context_api)
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

load_dotenv(os.path.join(_BASE_DIR, ".env"))

# Debug: verify .env loading
_env_test = os.getenv("JUDGE_MODEL", "NOT_LOADED")
print(f"[DEBUG] .env loading test - JUDGE_MODEL environment variable: {_env_test}", flush=True)

app = FastAPI()

# Startup event
@app.on_event("startup")
async def startup_event():
    print("\n" + "=" * 60, flush=True)
    print("[STARTUP] EvalMatrix Backend Started", flush=True)
    print("=" * 60, flush=True)
    print(f"[CONFIG] Judge Model: {JUDGE_CFG.model}", flush=True)
    print(f"[CONFIG] Judge API URL: {JUDGE_CFG.api_url}", flush=True)
    print(f"[CONFIG] Judge API Key Set: {bool(JUDGE_CFG.api_key)}", flush=True)
    if JUDGE_CFG.api_key:
        print(f"[CONFIG] Judge API Key Suffix: ...{JUDGE_CFG.api_key[-8:]}", flush=True)
    
    # Test actual config usage
    judge_cfg = get_judge_config()
    if judge_cfg:
        print(f"[CONFIG] Actual Judge Config:", flush=True)
        print(f"         Model: {judge_cfg['model']}", flush=True)
        print(f"         URL: {judge_cfg['url']}", flush=True)
        print(f"         Key: ...{judge_cfg['key'][-8:]}", flush=True)
    else:
        print("[ERROR] No Judge config available!", flush=True)
    
    print(f"[CONFIG] Batch API Enabled: {JUDGE_CFG.batch_api_enabled}", flush=True)
    print(f"[CONFIG] Prompt Cache Enabled: {JUDGE_CFG.prompt_cache_enabled}", flush=True)
    print("=" * 60 + "\n", flush=True)

# Global exception handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"[WARN] Request validation failed: {exc.errors()}")
    try:
        body = await request.json()
        print(f"[DEBUG] Request body: {body}")
    except Exception:
        pass
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class Product(BaseModel):
    id: str
    name: str
    type: str  # "openrouter" or "dify"
    url: str
    key: str
    code_snippet: Optional[str] = None
    model_name: Optional[str] = None

class ScenarioRequest(BaseModel):
    scenario: str

class EvaluationRequest(BaseModel):
    scenario: str
    question: Optional[str] = None
    dimensions: List[Dict[str, Any]]
    results: List[Dict[str, Any]]

class JudgeConfigUpdate(BaseModel):
    """Judge configuration update model for frontend"""
    api_key: Optional[str] = None
    model: Optional[str] = None
    api_url: Optional[str] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    concurrency: Optional[int] = None
    timeout: Optional[int] = None
    batch_api_enabled: Optional[bool] = None
    prompt_cache_enabled: Optional[bool] = None

# Judge model configuration
class JudgeConfig(BaseSettings):
    """
    Judge LLM global configuration with environment variable override support.
    
    Environment variable mapping:
    - JUDGE_API_KEY -> judge_api_key
    - JUDGE_MODEL -> judge_model
    - JUDGE_API_URL -> judge_api_url
    - etc.
    """

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        env_prefix="JUDGE_",
        case_sensitive=False,
        extra="ignore",
    )

    # Use lowercase field names, pydantic will map JUDGE_API_KEY -> judge_api_key
    api_key: str = ""
    model: str = "gemini-2.5-flash"
    api_url: str = "https://aigw.netease.com/v1/chat/completions"  # ? Default changed to NetEase

    context_window: int = 200000
    max_input_tokens: int = 200000
    max_output_tokens: int = 64000
    rpm_limit: int = 120
    tpm_limit: int = 200000
    concurrency: int = 3
    max_retries: int = 3
    base_retry_delay: float = 2.0
    timeout: int = 90
    batch_api_enabled: bool = False
    prompt_cache_enabled: bool = False


JUDGE_CFG = JudgeConfig()

# Log loaded configuration
print("=" * 60, flush=True)
print("[INFO] Judge configuration loaded:", flush=True)
print(f"  - Model: {JUDGE_CFG.model}", flush=True)
print(f"  - API URL: {JUDGE_CFG.api_url}", flush=True)
print(f"  - API Key: {'*' * 20}{JUDGE_CFG.api_key[-8:] if JUDGE_CFG.api_key else '(not set)'}", flush=True)
print("=" * 60, flush=True)

# Judge concurrency semaphore
_judge_semaphore: Optional[asyncio.Semaphore] = None

def get_judge_semaphore() -> asyncio.Semaphore:
    """Lazy-load Judge concurrency semaphore"""
    global _judge_semaphore
    if _judge_semaphore is None:
        _judge_semaphore = asyncio.Semaphore(JUDGE_CFG.concurrency)
    return _judge_semaphore

# Product configuration persistence
CONFIG_FILE = os.path.join(_BASE_DIR, "product_config.json")
JUDGE_CONFIG_FILE = os.path.join(_BASE_DIR, "judge_config.json")

def load_judge_config_from_file():
    """Load Judge configuration from judge_config.json if exists"""
    if os.path.exists(JUDGE_CONFIG_FILE):
        try:
            with open(JUDGE_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Update JUDGE_CFG with saved values
                if data.get("api_key"):
                    JUDGE_CFG.api_key = data["api_key"]
                if data.get("model"):
                    JUDGE_CFG.model = data["model"]
                if data.get("api_url"):
                    JUDGE_CFG.api_url = data["api_url"]
                if data.get("max_input_tokens"):
                    JUDGE_CFG.max_input_tokens = data["max_input_tokens"]
                if data.get("max_output_tokens"):
                    JUDGE_CFG.max_output_tokens = data["max_output_tokens"]
                if data.get("concurrency"):
                    JUDGE_CFG.concurrency = data["concurrency"]
                if data.get("timeout"):
                    JUDGE_CFG.timeout = data["timeout"]
                if "batch_api_enabled" in data:
                    JUDGE_CFG.batch_api_enabled = data["batch_api_enabled"]
                if "prompt_cache_enabled" in data:
                    JUDGE_CFG.prompt_cache_enabled = data["prompt_cache_enabled"]
                print(f"[INFO] Loaded Judge config from {JUDGE_CONFIG_FILE}")
                print(f"[INFO]   -> model={JUDGE_CFG.model}, prompt_cache={JUDGE_CFG.prompt_cache_enabled}, "
                      f"batch_api={JUDGE_CFG.batch_api_enabled}", flush=True)
        except Exception as e:
            print(f"[WARN] Failed to load Judge config file: {e}")

def save_judge_config():
    """Persist Judge configuration to judge_config.json"""
    try:
        data = {
            "api_key": JUDGE_CFG.api_key,
            "model": JUDGE_CFG.model,
            "api_url": JUDGE_CFG.api_url,
            "max_input_tokens": JUDGE_CFG.max_input_tokens,
            "max_output_tokens": JUDGE_CFG.max_output_tokens,
            "concurrency": JUDGE_CFG.concurrency,
            "timeout": JUDGE_CFG.timeout,
            "batch_api_enabled": JUDGE_CFG.batch_api_enabled,
            "prompt_cache_enabled": JUDGE_CFG.prompt_cache_enabled,
        }
        with open(JUDGE_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Saved Judge config to {JUDGE_CONFIG_FILE}")
    except Exception as e:
        print(f"[ERROR] Failed to save Judge config: {e}")

# Load Judge config from file on startup (overrides .env if exists)
load_judge_config_from_file()

def load_products() -> Dict[str, Product]:
    """Load product configuration from local JSON file"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {p["id"]: Product(**p) for p in data}
        except Exception as e:
            print(f"[ERROR] Failed to load product config: {e}")
    return {}

def save_products():
    """Persist product configuration to local JSON file"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump([p.dict() for p in PRODUCTS.values()], f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Failed to save product config: {e}")

# ── Evaluation abort mechanism ──
# Active evaluation IDs and their cancellation flags
_eval_abort_flags: Dict[str, bool] = {}

def create_eval_session() -> str:
    """Create a new evaluation session, return session_id"""
    import uuid
    sid = uuid.uuid4().hex[:12]
    _eval_abort_flags[sid] = False
    return sid

def is_eval_aborted(session_id: str) -> bool:
    """Check if an evaluation session has been aborted"""
    return _eval_abort_flags.get(session_id, True)

def abort_eval_session(session_id: str) -> bool:
    """Abort an evaluation session"""
    if session_id in _eval_abort_flags:
        _eval_abort_flags[session_id] = True
        print(f"[INFO] Evaluation session {session_id} aborted", flush=True)
        return True
    return False

def cleanup_eval_session(session_id: str):
    """Clean up evaluation session"""
    _eval_abort_flags.pop(session_id, None)

# In-memory product dictionary
PRODUCTS: Dict[str, Product] = load_products()

# Initialize with default products if empty
if not PRODUCTS:
    PRODUCTS["1"] = Product(
        id="1", 
        name="OpenRouter (Gemini)", 
        type="openrouter", 
        url="https://openrouter.ai/api/v1/chat/completions",
        key=os.getenv("OPENROUTER_API_KEY", ""),
        model_name="google/gemini-2.5-flash"
    )
    PRODUCTS["2"] = Product(
        id="2", 
        name="Dify Workflow", 
        type="dify", 
        url=os.getenv("DIFY_API_URL", ""),
        key=os.getenv("DIFY_API_KEY", "")
    )
    save_products()

# Evaluation result archiving
EVAL_ARCHIVE_FILE = os.path.join(_BASE_DIR, "eval_archive.xlsx")

def archive_eval_result(
    row_idx: int,
    question: str,
    evaluations: Dict[str, Any],
    answers: Dict[str, str],
    scenario: str,
    dimensions: List[Dict[str, Any]]
):
    """
    Archive evaluation results to eval_archive.xlsx
    
    Each record includes: timestamp, scenario, question, scores per dimension, reasoning, answers
    """
    try:
        records = []
        # Fixed: Use ISO format timestamp compatible with Excel
        timestamp = datetime.now().isoformat(timespec='seconds')

        for pid, eval_data in evaluations.items():
            scores = eval_data.get("scores", {})
            reasoning = eval_data.get("reasoning", "")
            answer = answers.get(pid, "")

            record: Dict[str, Any] = {
                "timestamp": timestamp,
                "scenario": scenario,
                "row_idx": row_idx,
                "question": question,
                "product_id": pid,
                "reasoning": reasoning,
                "answer": answer,
            }
            # Flatten dimension scores to columns
            for dim in dimensions:
                dname = dim["name"]
                record[f"score_{dname}"] = scores.get(dname, None)

            records.append(record)

        if not records:
            return

        new_df = pd.DataFrame(records)

        # Fixed: Safer file read/write with better error handling
        try:
            # Append to existing file
            if os.path.exists(EVAL_ARCHIVE_FILE):
                existing_df = pd.read_excel(EVAL_ARCHIVE_FILE, engine='openpyxl')
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            else:
                combined_df = new_df

            combined_df.to_excel(EVAL_ARCHIVE_FILE, index=False, engine='openpyxl')
            print(f"[INFO] Archived row {row_idx} evaluation result -> {EVAL_ARCHIVE_FILE}")
        except PermissionError:
            print(f"[ERROR] Cannot write to {EVAL_ARCHIVE_FILE} - file may be open in Excel")
        except Exception as write_err:
            print(f"[ERROR] Failed to write Excel: {write_err}")
            # Try backup write
            backup_file = EVAL_ARCHIVE_FILE.replace('.xlsx', f'_backup_{int(time.time())}.xlsx')
            combined_df.to_excel(backup_file, index=False, engine='openpyxl')
            print(f"[INFO] Written to backup file: {backup_file}")

    except Exception as e:
        print(f"[WARN] Failed to archive evaluation result (row={row_idx}): {e}\n{traceback.format_exc()}")

# Product management API
@app.get("/api/config/judge")
def get_judge_config_api():
    """Get current Judge configuration for frontend"""
    return {
        "model": JUDGE_CFG.model,
        "api_url": JUDGE_CFG.api_url,
        "api_key_set": bool(JUDGE_CFG.api_key),
        "api_key_suffix": JUDGE_CFG.api_key[-8:] if JUDGE_CFG.api_key else "",
        "max_input_tokens": JUDGE_CFG.max_input_tokens,
        "max_output_tokens": JUDGE_CFG.max_output_tokens,
        "concurrency": JUDGE_CFG.concurrency,
        "timeout": JUDGE_CFG.timeout,
        "batch_api_enabled": JUDGE_CFG.batch_api_enabled,
        "prompt_cache_enabled": JUDGE_CFG.prompt_cache_enabled,
    }

@app.put("/api/config/judge")
def update_judge_config_api(config: JudgeConfigUpdate):
    """Update Judge configuration from frontend"""
    try:
        if config.api_key is not None:
            JUDGE_CFG.api_key = config.api_key
        if config.model is not None:
            JUDGE_CFG.model = config.model
        if config.api_url is not None:
            JUDGE_CFG.api_url = config.api_url
        if config.max_input_tokens is not None:
            JUDGE_CFG.max_input_tokens = config.max_input_tokens
        if config.max_output_tokens is not None:
            JUDGE_CFG.max_output_tokens = config.max_output_tokens
        if config.concurrency is not None:
            JUDGE_CFG.concurrency = config.concurrency
            # Reset semaphore when concurrency changes
            global _judge_semaphore
            _judge_semaphore = None
        if config.timeout is not None:
            JUDGE_CFG.timeout = config.timeout
        if config.batch_api_enabled is not None:
            JUDGE_CFG.batch_api_enabled = config.batch_api_enabled
        if config.prompt_cache_enabled is not None:
            JUDGE_CFG.prompt_cache_enabled = config.prompt_cache_enabled
        
        # Persist to file
        save_judge_config()
        
        print(f"[INFO] Judge config updated: model={JUDGE_CFG.model}, url={JUDGE_CFG.api_url}, "
              f"batch_api={JUDGE_CFG.batch_api_enabled}, prompt_cache={JUDGE_CFG.prompt_cache_enabled}")
        
        return {
            "status": "success",
            "message": "Judge configuration updated",
            "config": {
                "model": JUDGE_CFG.model,
                "api_url": JUDGE_CFG.api_url,
                "api_key_set": bool(JUDGE_CFG.api_key),
                "max_input_tokens": JUDGE_CFG.max_input_tokens,
                "max_output_tokens": JUDGE_CFG.max_output_tokens,
                "concurrency": JUDGE_CFG.concurrency,
                "timeout": JUDGE_CFG.timeout,
                "batch_api_enabled": JUDGE_CFG.batch_api_enabled,
                "prompt_cache_enabled": JUDGE_CFG.prompt_cache_enabled,
            }
        }
    except Exception as e:
        print(f"[ERROR] Failed to update Judge config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products")
def get_products():
    return list(PRODUCTS.values())

@app.post("/api/products")
def add_product(product: Product):
    """Add or update product configuration with auto-parsing from code snippet"""
    if product.code_snippet:
        snippet = product.code_snippet
        snippet_lower = snippet.lower()
        
        # Auto-detect product type
        if "messages" in snippet_lower and "role" in snippet_lower:
             product.type = "openrouter"
        elif "inputs" in snippet_lower or "workflow" in snippet_lower:
             product.type = "dify"
        else:
             product.type = "openrouter"
        
        # Extract model name (OpenRouter only)
        if product.type == "openrouter":
            try:
                match = re.search(
                    r'(?:[\'"]model[\'"]|model)\s*:\s*[\'"]([^\'"]+)[\'"]', snippet
                )
                if match:
                    product.model_name = match.group(1)
            except Exception:
                pass
        
        # Extract API key from snippet if not provided
        if not product.key or not product.key.strip():
            try:
                key_match = re.search(r'(sk-[a-zA-Z0-9\-_]+)', snippet)
                if key_match:
                    product.key = key_match.group(1)
            except Exception:
                pass

    # Remove "Bearer " prefix from key
    if product.key and product.key.lower().startswith("bearer "):
        product.key = product.key[7:].strip()
    
    # Default model fallback
    if product.type == "openrouter" and not product.model_name:
        product.model_name = "google/gemini-2.5-flash"

    PRODUCTS[product.id] = product
    save_products()
    return product

@app.delete("/api/products/{product_id}")
def delete_product(product_id: str):
    if product_id not in PRODUCTS:
        raise HTTPException(status_code=404, detail="Product not found")
    del PRODUCTS[product_id]
    save_products()
    return {"status": "success", "message": "Product deleted"}

def get_judge_config() -> Optional[Dict[str, Any]]:
    """
    Get Judge LLM configuration.
    
    FORCED: Only use environment variables to avoid confusion.
    """
    if JUDGE_CFG.api_key and JUDGE_CFG.api_key.strip():
        return {
            "key": JUDGE_CFG.api_key,
            "model": JUDGE_CFG.model,
            "url": JUDGE_CFG.api_url,
        }
    
    print("[ERROR] JUDGE_API_KEY is not set in .env! No Judge available.", flush=True)
    return None

def load_judge_prompt(
    scenario: str,
    dimensions_text: str,
    products_text: str,
    question: Optional[str] = None
) -> str:
    """Load Judge prompt template from judge_prompt.txt or use built-in default"""
    prompt_file = os.path.join(_BASE_DIR, "judge_prompt.txt")
    template = ""

    if os.path.exists(prompt_file):
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                template = f.read()
        except Exception as e:
            print(f"[WARN] Failed to read judge_prompt.txt, using default template: {e}")

    if not template:
        template = """
You are an expert AI Judge.

Scenario: {scenario}

{question_section}

Evaluation Dimensions:
{dimensions_text}

Product Answers:
{products_text}

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
            "reasoning": "Brief explanation..."
        }},
        ...
    }}
}}
Important:
1. The keys in "evaluations" MUST be the exact Product IDs strings provided in the input.
2. "scores" values must match the dimension type (int for scale, bool for binary, string for categorical).
3. Output strictly valid JSON, no extra text outside the JSON block.
"""

    question_section = f"User Question/Input: {question}" if question else ""

    try:
        return template.format(
            scenario=scenario,
            dimensions_text=dimensions_text,
            products_text=products_text,
            question=question or "",
            question_section=question_section,
        )
    except KeyError as e:
        print(f"[WARN] judge_prompt.txt has unknown placeholder {e}, check template format")
        return template

# Dimension presets management
DIMENSIONS_FILE = os.path.join(_BASE_DIR, "dimension_presets.json")
DIMENSION_PRESETS: Dict[str, Any] = {}

if os.path.exists(DIMENSIONS_FILE):
    try:
        with open(DIMENSIONS_FILE, "r", encoding="utf-8") as f:
            DIMENSION_PRESETS = json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to load dimension presets: {e}")

@app.get("/api/dimension-presets")
def get_dimension_presets():
    return DIMENSION_PRESETS

@app.post("/api/dimension-presets")
def save_dimension_preset(name: str = Form(...), dimensions: str = Form(...)):
    """Save evaluation dimension preset to local file"""
    try:
        dims = json.loads(dimensions)
        DIMENSION_PRESETS[name] = dims
        with open(DIMENSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(DIMENSION_PRESETS, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Dimension preset '{name}' saved")
        return {"status": "success", "message": "Preset saved"}
    except json.JSONDecodeError as e:
        print(f"[ERROR] Dimension preset JSON parse failed: {e}")
        return {"error": f"Dimension JSON format error: {str(e)}", "status": "failed"}
    except Exception as e:
        print(f"[ERROR] Failed to save dimension preset: {e}\n{traceback.format_exc()}")
        return {"error": str(e), "status": "failed"}

# Math analysis tools: Entropy weighting & PCA
def calculate_entropy_weights(scores_df: pd.DataFrame) -> Dict[str, float]:
    """
    Calculate dimension weights using entropy method.
    Input: rows=models, columns=dimensions, values=numeric.
    Returns: {dimension_name: weight}
    """
    try:
        if scores_df.empty:
            return {}

        scaler = MinMaxScaler()
        normalized = scaler.fit_transform(scores_df) + 1e-5

        col_sums = normalized.sum(axis=0)
        col_sums[col_sums == 0] = 1e-5
        P = normalized / col_sums

        k = 1 / np.log(max(len(scores_df), 2))
        E = -k * (P * np.log(P)).sum(axis=0)

        d = 1 - E
        d_sum = d.sum()

        W = (
            np.ones(len(scores_df.columns)) / len(scores_df.columns)
            if d_sum == 0
            else d / d_sum
        )

        return {
            col: 0.0 if (np.isnan(w) or np.isinf(w)) else float(round(w, 4))
            for col, w in zip(scores_df.columns, W)
        }
    except Exception as e:
        print(f"[ERROR] Entropy weight calculation failed: {e}\n{traceback.format_exc()}")
        n = len(scores_df.columns)
        return {col: round(1 / n, 4) for col in scores_df.columns}

def calculate_pca_score(scores_df: pd.DataFrame) -> Dict[str, float]:
    """
    Calculate composite score using PCA first principal component (normalized 0-10).
    """
    try:
        if len(scores_df) < 2:
            print("[WARN] PCA requires at least 2 model samples, skipping")
            return {idx: 0.0 for idx in scores_df.index}

        pca = PCA(n_components=1)
        components = pca.fit_transform(scores_df)

        scaler = MinMaxScaler(feature_range=(0, 10))
        scaled = scaler.fit_transform(components)

        return {
            idx: 0.0 if (np.isnan(v[0]) or np.isinf(v[0])) else round(float(v[0]), 2)
            for idx, v in zip(scores_df.index, scaled)
        }
    except Exception as e:
        print(f"[ERROR] PCA calculation failed: {e}\n{traceback.format_exc()}")
        return {idx: 0.0 for idx in scores_df.index}

# Continue in next part...
# Part 2: Core evaluation logic and product calling

async def process_evaluation(
    scenario: str,
    dimensions: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    question: Optional[str] = None,
    row_idx: int = -1,
) -> Dict[str, Any]:
    """
    Call Judge model once for a single data entry evaluation.
    
    Common failure reasons:
    - "No Judge configuration" -> .env missing JUDGE_API_KEY or no OpenRouter product
    - HTTP 401 -> Invalid API Key
    - HTTP 429 -> Exceeds RPM/TPM limit, increase retry interval or reduce concurrency
    - HTTP 400 context_length -> Prompt too long, reduce MAX_INPUT_TOKENS
    - JSON parse failed -> Model didn't follow format, check Prompt instructions
    - evaluations empty -> Model returned JSON but key mismatch, check product_id
    - score type wrong -> Model returned string number, auto-convert with float()
    """
    judge_config = get_judge_config()
    if not judge_config:
        msg = "No Judge config found, please set JUDGE_API_KEY in .env or add OpenRouter product"
        print(f"[ERROR] {msg}")
        return {"error": msg, "status": "failed"}

    # Build products_text including product ID and answer
    products_text = ""
    for res in results:
        input_ctx = f"Input: {res['input']}\n" if res.get("input") else ""
        products_text += (
            f"Product ID: {res.get('product_id', 'Unknown')}\n"
            f"{input_ctx}"
            f"Answer: {res.get('answer', 'No Answer')}\n\n"
        )

    dimensions_text = json.dumps(dimensions, indent=2, ensure_ascii=False)
    prompt = load_judge_prompt(scenario, dimensions_text, products_text, question=question)

    # Rough estimate prompt token count (4 chars = 1 token), truncate if exceeds limit
    estimated_tokens = len(prompt) // 4
    if estimated_tokens > JUDGE_CFG.max_input_tokens:
        print(
            f"[WARN] Row {row_idx}: Estimated input Token ({estimated_tokens}) "
            f"exceeds limit ({JUDGE_CFG.max_input_tokens}), Prompt will be truncated"
        )
        max_chars = JUDGE_CFG.max_input_tokens * 4
        prompt = prompt[:max_chars]

    # Build prompt cache parts if enabled and Claude model
    use_cache = JUDGE_CFG.prompt_cache_enabled
    cache_payload = None
    
    # 根据模型自动限制 max_output_tokens
    from judge_caller_factory import get_safe_max_output_tokens, _supports_temperature
    safe_max_tokens = get_safe_max_output_tokens(judge_config["model"], JUDGE_CFG.max_output_tokens)
    temp_param = {"temperature": 0.2} if _supports_temperature(judge_config["model"]) else {}
    
    if use_cache:
        try:
            from prompt_cache_builder import build_cached_single_prompt_parts, is_claude_model
            if is_claude_model(judge_config["model"]):
                system_parts, user_msgs = build_cached_single_prompt_parts(
                    scenario=scenario,
                    dimensions_text=dimensions_text,
                    products_text=products_text,
                    question=question,
                )
                cache_payload = {
                    "model": judge_config["model"],
                    "system": system_parts,
                    "messages": user_msgs,
                    "max_tokens": safe_max_tokens,
                    **temp_param,
                }
        except Exception as e:
            print(f"[WARN] Row {row_idx} Prompt cache build failed, falling back: {e}")
            cache_payload = None

    sem = get_judge_semaphore()

    for attempt in range(JUDGE_CFG.max_retries):
        try:
            async with sem:
                loop = asyncio.get_event_loop()
                # ── 强制日志确认实际调用的目标 ──
                print(f"\n[JUDGE_REQUEST] Row {row_idx} | Model: {judge_config['model']} | URL: {judge_config['url']} | Cache: {cache_payload is not None}", flush=True)
                
                request_json = cache_payload if cache_payload else {
                    "model": judge_config["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": safe_max_tokens,
                    **temp_param,
                }
                
                resp = await loop.run_in_executor(
                    None,
                    lambda: requests.post(
                        judge_config["url"],
                        headers={
                            "Authorization": f"Bearer {judge_config['key']}",
                            "Content-Type": "application/json",
                        },
                        json=request_json,
                        timeout=JUDGE_CFG.timeout,
                    ),
                )

            # HTTP error handling
            if resp.status_code == 429:
                wait = JUDGE_CFG.base_retry_delay * (2 ** attempt)
                print(f"[WARN] Row {row_idx} Judge rate limit (429), retry in {wait}s (attempt {attempt+1})")
                await asyncio.sleep(wait)
                continue

            if resp.status_code == 400:
                err_body = resp.text
                print(f"[ERROR] Row {row_idx} Judge 400 error: {err_body[:500]}")
                
                # 自动处理不支持 temperature 的情况
                if "temperature" in err_body.lower() and "temperature" in request_json:
                    print(f"[WARN] Row {row_idx} 模型不支持 temperature，自动去除后重试", flush=True)
                    del request_json["temperature"]
                    continue
                
                return {"error": f"Judge API 400 error: {err_body}", "status": "failed"}

            if not resp.ok:
                print(f"[WARN] Row {row_idx} Judge HTTP {resp.status_code}: {resp.text}")
                if attempt < JUDGE_CFG.max_retries - 1:
                    await asyncio.sleep(JUDGE_CFG.base_retry_delay * (2 ** attempt))
                    continue
                return {
                    "error": f"Judge API error (HTTP {resp.status_code}): {resp.text}",
                    "status": "failed",
                }

            # Parse response
            raw_data = resp.json()
            try:
                content = raw_data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                print(f"[ERROR] Row {row_idx} Response structure abnormal, cannot extract content: {e}\nRaw: {raw_data}")
                content = json.dumps(raw_data)

            print(f"[DEBUG] Row {row_idx} Judge raw response: {content[:500]}...")

            # Remove Markdown code block wrapper
            content_clean = re.sub(r"```(?:json)?", "", content).replace("```", "").strip()

            # JSON parsing (with fallback)
            try:
                parsed = json.loads(content_clean)
            except json.JSONDecodeError:
                # Try extracting JSON object from mixed text
                start = content_clean.find("{")
                end = content_clean.rfind("}")
                if start != -1 and end != -1:
                    try:
                        parsed = json.loads(content_clean[start:end + 1])
                    except json.JSONDecodeError as je:
                        print(f"[ERROR] Row {row_idx} JSON second parse failed: {je}\nFragment: {content_clean[start:end+1][:300]}")
                        if attempt < JUDGE_CFG.max_retries - 1:
                            await asyncio.sleep(JUDGE_CFG.base_retry_delay)
                            continue
                        return {"error": "Judge returned content cannot be parsed as JSON, check Prompt format instructions", "status": "failed"}
                else:
                    print(f"[ERROR] Row {row_idx} No JSON object found in response: {content_clean[:300]}")
                    if attempt < JUDGE_CFG.max_retries - 1:
                        await asyncio.sleep(JUDGE_CFG.base_retry_delay)
                        continue
                    return {"error": "Judge response contains no valid JSON", "status": "failed"}

            # Extract evaluations
            evals = parsed.get("evaluations", {})

            # If top-level is directly evaluation map (model didn't wrap evaluations key), auto-recognize
            if not evals and isinstance(parsed, dict):
                first_val = next(iter(parsed.values()), None)
                if isinstance(first_val, dict) and "scores" in first_val:
                    evals = parsed
                    print(f"[WARN] Row {row_idx} Judge didn't return 'evaluations' key, auto-recognized top-level structure")

            if not evals:
                print(f"[WARN] Row {row_idx} evaluations empty, raw content: {content_clean[:300]}")
                if attempt < JUDGE_CFG.max_retries - 1:
                    await asyncio.sleep(JUDGE_CFG.base_retry_delay)
                    continue
                return {"error": "Judge returned empty evaluations", "status": "failed"}

            # Key mapping: handle case where model returns product name instead of ID
            name_to_id = {p.name.lower(): p.id for p in PRODUCTS.values()}
            final_evals: Dict[str, Any] = {}

            for k, v in evals.items():
                if k in PRODUCTS:
                    final_evals[k] = v
                elif k.lower() in name_to_id:
                    mapped_id = name_to_id[k.lower()]
                    print(f"[INFO] Row {row_idx} Key '{k}' mapped to product ID '{mapped_id}'")
                    final_evals[mapped_id] = v
                else:
                    # judge-only mode: key is Excel column name, keep as-is
                    final_evals[k] = v

            # Archive single evaluation result
            answers_map = {r.get("product_id", ""): r.get("answer", "") for r in results}
            archive_eval_result(
                row_idx=row_idx,
                question=question or "",
                evaluations=final_evals,
                answers=answers_map,
                scenario=scenario,
                dimensions=dimensions,
            )

            return {"status": "success", "evaluations": final_evals, "raw_response": content}

        except requests.exceptions.Timeout:
            print(f"[WARN] Row {row_idx} Judge request timeout (attempt {attempt+1}/{JUDGE_CFG.max_retries})")
            if attempt < JUDGE_CFG.max_retries - 1:
                await asyncio.sleep(JUDGE_CFG.base_retry_delay * (2 ** attempt))
            else:
                return {"error": f"Judge request timeout ({JUDGE_CFG.timeout}s), increase JUDGE_TIMEOUT or reduce input length", "status": "failed"}

        except Exception as e:
            print(f"[ERROR] Row {row_idx} Judge exception (attempt {attempt+1}): {e}\n{traceback.format_exc()}")
            if attempt < JUDGE_CFG.max_retries - 1:
                await asyncio.sleep(JUDGE_CFG.base_retry_delay * (2 ** attempt))
            else:
                return {"error": f"Evaluation failed (retried {JUDGE_CFG.max_retries} times): {str(e)}", "status": "failed"}

    return {"error": "Evaluation failed: exceeded max retries", "status": "failed"}

async def process_evaluation_task(
    scenario: str,
    dims: List[Dict[str, Any]],
    row_test_results: List[Dict[str, Any]],
    idx: int,
    text: str,
) -> Dict[str, Any]:
    """
    Wrap single-row evaluation logic, catch exceptions to prevent gather interruption.
    Each data entry independently calls Judge model once.
    """
    try:
        eval_resp = await process_evaluation(
            scenario, dims, row_test_results, question=text, row_idx=idx
        )
    except Exception as e:
        print(f"[ERROR] process_evaluation_task Row {idx} uncaught exception: {e}\n{traceback.format_exc()}")
        eval_resp = {"status": "failed", "error": str(e)}

    base = {
        "row": idx,
        "question": text,
        "answers": {r["product_id"]: r.get("answer", "") for r in row_test_results},
        "latencies": {r["product_id"]: r.get("latency", 0) for r in row_test_results},
    }

    if eval_resp.get("status") == "success":
        base["evaluations"] = eval_resp.get("evaluations", {})
    else:
        base["error"] = eval_resp.get("error", "Unknown evaluation error")
        base["evaluations"] = {}

    return base

# Product calling routing
async def process_test_request(
    product: Product,
    text: str,
    final_image_inputs: List[str],
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Route to corresponding product type calling function"""
    target_url = api_url or product.url
    target_key = api_key or product.key

    try:
        if product.type == "openrouter":
            model = product.model_name or "google/gemini-2.5-flash"
            return await call_openrouter(target_url, target_key, text, final_image_inputs, model)
        elif product.type == "dify":
            first_image = final_image_inputs[0] if final_image_inputs else None
            return await call_dify(target_url, target_key, text, first_image or "")
        else:
            return {"error": f"Unknown product type: {product.type}", "status": "failed"}
    except Exception as e:
        print(f"[ERROR] process_test_request exception: {e}\n{traceback.format_exc()}")
        return {"error": str(e), "status": "failed"}

# Single product test API
@app.post("/api/test")
async def test_product(
    text: str = Form(...),
    images: List[UploadFile] = File(None),
    image_urls: Optional[str] = Form(None),
    product_id: str = Form(...),
    api_key: Optional[str] = Form(None),
    api_url: Optional[str] = Form(None),
):
    """Test specified product, supports text + images (URL or uploaded file)"""
    product = PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

    final_image_inputs: List[str] = []

    # Parse image URLs (comma or newline separated)
    if image_urls:
        urls = [u.strip() for u in re.split(r"[,\n]", image_urls) if u.strip()]
        final_image_inputs.extend(urls)

    # Process uploaded images (convert to Base64)
    if images:
        for img in images:
            contents = await img.read()
            b64 = base64.b64encode(contents).decode("utf-8")
            mime = img.content_type or "image/jpeg"
            final_image_inputs.append(f"data:{mime};base64,{b64}")

    return await process_test_request(product, text, final_image_inputs, api_url, api_key)

# Dimension auto-generation API
@app.post("/api/generate-dimensions")
async def generate_dimensions(request: ScenarioRequest):
    """Auto-generate evaluation dimensions based on scenario description using Judge model"""
    judge_config = get_judge_config()
    if not judge_config:
        return {"error": "No Judge config found, please set JUDGE_API_KEY", "status": "failed"}
    
    prompt = f"""
    You are an expert Product Manager. 
    Given the following user scenario for an AI product:
    "{request.scenario}"
    
    Please generate some evaluation dimensions for testing this product.
    Include a mix of Quantitative (Scale) and Qualitative (Binary/Categorical) dimensions.
    
    For each dimension, provide:
    1. 'name': Short name.
    2. 'description': Brief explanation.
    3. 'weight': Integer 1-10.
    4. 'type': One of ["scale", "binary", "categorical"].
       - "scale": Numeric score 1-10.
       - "binary": True/False (e.g. "Has Hallucination").
       - "categorical": Select from tags (e.g. "Tone": ["Formal", "Casual"]).
    5. 'options': List of strings (ONLY for 'categorical' type, else null).
    
    Return the result ONLY as a JSON object with a key 'dimensions' containing the list.
    Example: 
    {{ "dimensions": [ 
        {{ "name": "Accuracy", "description": "...", "weight": 9, "type": "scale" }},
        {{ "name": "Has Error", "description": "...", "weight": 5, "type": "binary" }},
        {{ "name": "Tone", "description": "...", "weight": 3, "type": "categorical", "options": ["Happy", "Sad"] }}
    ] }}
    """

    try:
        resp = await call_openrouter(
            judge_config["url"], judge_config["key"], prompt, None, judge_config["model"]
        )
        if "error" in resp:
            return resp

        content = re.sub(r"```(?:json)?", "", resp.get("answer", "")).replace("```", "").strip()
        data = json.loads(content)
        return {"status": "success", "dimensions": data.get("dimensions", [])}
    except Exception as e:
        print(f"[ERROR] generate_dimensions failed: {e}\n{traceback.format_exc()}")
        return {"error": str(e), "status": "failed"}

# Continue in next part for batch/judge-only endpoints and helper functions...
# Part 3: Batch evaluation + Judge-only + OpenRouter/Dify calling

# Batch test + evaluation (Excel input)
@app.post("/api/batch-evaluate-excel")
async def batch_evaluate_excel(
    file: UploadFile = File(...),
    product_ids: str = Form(...),
    scenario: str = Form(...),
    dimensions: str = Form(...),
    eval_session_id: Optional[str] = Form(None),  # New: abort support
):
    """Read Excel, call each product and evaluate for each row, each entry independently triggers one Judge call"""
    pids = [p.strip() for p in product_ids.split(",") if p.strip()]
    target_products = [PRODUCTS[pid] for pid in pids if pid in PRODUCTS]

    if not target_products:
        return {"error": "No valid product IDs found", "status": "failed"}

    try:
        dims = json.loads(dimensions)
    except json.JSONDecodeError as e:
        return {"error": f"dimensions JSON format error: {e}", "status": "failed"}

    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as e:
        return {"error": f"Excel read failed: {str(e)}", "status": "failed"}

    # Process each row: concurrent product calls + independent Judge evaluation
    async def process_row(idx: int, row: pd.Series) -> Dict[str, Any]:
        text = str(row.iloc[0])
        if not text.strip():
            return {"row": idx, "question": text, "error": "Empty row skipped", "evaluations": {}, "answers": {}, "latencies": {}}

        image_inputs: List[str] = []
        for col_idx in range(1, len(row)):
            val = row.iloc[col_idx]
            if pd.isna(val) or not str(val).strip():
                continue
            val = str(val).strip()
            if val.startswith("http"):
                image_inputs.append(val)
            elif os.path.exists(val):
                try:
                    with open(val, "rb") as img_f:
                        b64 = base64.b64encode(img_f.read()).decode("utf-8")
                        ext = os.path.splitext(val)[1].lower()
                        mime = "image/png" if ext == ".png" else "image/jpeg"
                        image_inputs.append(f"data:{mime};base64,{b64}")
                except Exception as e:
                    print(f"[WARN] Row {idx} image read failed {val}: {e}")

        # Concurrently call all products
        product_results = await asyncio.gather(
            *[process_test_request(p, text, image_inputs) for p in target_products]
        )

        row_test_results = [
            {
                "product_id": target_products[i].id,
                "answer": product_results[i].get("answer", "Error"),
                "latency": product_results[i].get("latency", 0),
                "input": text,
            }
            for i in range(len(target_products))
        ]

        # Each entry independently calls Judge once
        return await process_evaluation_task(scenario, dims, row_test_results, idx, text)

    # Use Semaphore to control concurrent rows, prevent Judge RPM overload
    batch_sem = asyncio.Semaphore(JUDGE_CFG.concurrency)

    async def process_row_with_sem(idx: int, row: pd.Series) -> Dict[str, Any]:
        async with batch_sem:
            return await process_row(idx, row)

    tasks = [process_row_with_sem(idx, row) for idx, row in df.iterrows()]
    results = await asyncio.gather(*tasks)
    row_details = sorted(results, key=lambda x: x["row"])

    # Aggregate results
    frontend_aggregated: Dict[str, Any] = {}
    model_dim_avgs: Dict[str, Dict[str, Any]] = {}
    valid_numeric_dims = [d["name"] for d in dims if d.get("type", "scale") in ["scale", "binary"]]

    for pid in pids:
        pid_runs = []
        for row in row_details:
            evals = row.get("evaluations", {})
            eval_data = evals.get(pid, {}).copy() if evals else {}
            eval_data["answer"] = row.get("answers", {}).get(pid, "No Answer")
            eval_data["latency"] = row.get("latencies", {}).get(pid, 0)
            eval_data["question"] = row.get("question", "")
            if not eval_data.get("scores"):
                eval_data["scores"] = {}
                eval_data["reasoning"] = row.get("error", "Failed or Missing")
            pid_runs.append(eval_data)

        avg_scores: Dict[str, Any] = {}
        distribution: Dict[str, Any] = {}

        for d in dims:
            dname = d["name"]
            dtype = d.get("type", "scale")
            raw_vals = [r["scores"].get(dname) for r in pid_runs if r.get("scores", {}).get(dname) is not None]

            if not raw_vals:
                avg_scores[dname] = 0
                continue

            if dtype == "scale":
                try:
                    vals = [float(v) for v in raw_vals if str(v).replace(".", "", 1).lstrip("-").isdigit()]
                    avg_scores[dname] = round(sum(vals) / len(vals), 1) if vals else 0
                except Exception:
                    avg_scores[dname] = 0
            elif dtype == "binary":
                true_count = sum(1 for v in raw_vals if str(v).lower() in ["true", "1", "yes"])
                avg_scores[dname] = round(true_count / len(raw_vals), 2)
            elif dtype == "categorical":
                counts: Dict[str, int] = {}
                for v in raw_vals:
                    counts[str(v)] = counts.get(str(v), 0) + 1
                distribution[dname] = counts
                avg_scores[dname] = max(counts, key=counts.get) if counts else ""

        model_dim_avgs[pid] = avg_scores
        frontend_aggregated[pid] = {
            "avgScores": avg_scores,
            "distribution": distribution,
            "runs": pid_runs,
            "sampleReasoning": pid_runs[-1].get("reasoning", "") if pid_runs else "",
            "sampleAnswer": pid_runs[-1].get("answer", "") if pid_runs else "",
            "allAnswers": [r.get("answer", "") for r in pid_runs],
            "allLatencies": [r.get("latency", 0) for r in pid_runs],
            "allQuestions": [r.get("question", "") for r in pid_runs],
        }

    # Advanced analysis (entropy weighting & PCA)
    advanced_stats: Dict[str, Any] = {"entropy_weights": {}, "pca_scores": {}}
    try:
        data_for_math = []
        rows_index = []
        for pid in pids:
            row_data = {
                dname: model_dim_avgs[pid][dname]
                for dname in valid_numeric_dims
                if isinstance(model_dim_avgs.get(pid, {}).get(dname), (int, float))
            }
            if row_data:
                data_for_math.append(row_data)
                rows_index.append(pid)

        if data_for_math:
            df_math = pd.DataFrame(data_for_math, index=rows_index).fillna(0)
            advanced_stats["entropy_weights"] = calculate_entropy_weights(df_math)
            advanced_stats["pca_scores"] = calculate_pca_score(df_math)
    except Exception as e:
        print(f"[ERROR] Advanced analysis failed: {e}\n{traceback.format_exc()}")

    return {"status": "success", "results": frontend_aggregated, "advanced_stats": advanced_stats}

# ============================================================
# Helper functions for JSON/JSONL file reading
# ============================================================

async def read_json_file(file: UploadFile) -> pd.DataFrame:
    """
    Read JSON file and convert to DataFrame.
    Supports multiple formats:
    - [...] - direct array
    - {"data": [...]} - wrapped in 'data' key
    - {"records": {...}} - object of objects (converts to array)
    - {"key1": {...}, "key2": {...}} - top-level object (converts to array)
    
    Handles nested JSON fields by converting them to strings.
    """
    try:
        content = await file.read()
        data = json.loads(content.decode('utf-8'))
        
        records = []
        
        if isinstance(data, list):
            # Direct array: [...]
            records = data
        elif isinstance(data, dict):
            # Check for common wrapper keys
            if "data" in data and isinstance(data["data"], list):
                # {"data": [...]}
                records = data["data"]
            elif "data" in data and isinstance(data["data"], dict):
                # {"data": {"key1": {...}, "key2": {...}}}
                records = list(data["data"].values())
            elif "records" in data and isinstance(data["records"], dict):
                # {"records": {"key1": {...}, "key2": {...}}}
                # Convert object of objects to array, preserve keys as id
                for key, value in data["records"].items():
                    if isinstance(value, dict):
                        value["_record_id"] = key  # Preserve original key
                        records.append(value)
                    else:
                        records.append({"_record_id": key, "value": value})
            elif "records" in data and isinstance(data["records"], list):
                # {"records": [...]}
                records = data["records"]
            else:
                # Top-level object: {"key1": {...}, "key2": {...}}
                # Assume all top-level keys are records (except 'meta', 'metadata', 'info')
                exclude_keys = {"meta", "metadata", "info", "config", "settings"}
                potential_records = {k: v for k, v in data.items() if k not in exclude_keys and isinstance(v, dict)}
                
                if potential_records:
                    for key, value in potential_records.items():
                        value["_record_id"] = key
                        records.append(value)
                else:
                    # Fallback: treat the whole object as a single record
                    records = [data]
        else:
            raise ValueError("JSON must be an array or object")
        
        if not records:
            raise ValueError("JSON file contains no records")
        
        # Flatten nested JSON fields
        flattened_records = []
        for record in records:
            flattened = {}
            for key, value in record.items():
                if isinstance(value, (dict, list)):
                    # Convert nested structures to JSON string
                    flattened[key] = json.dumps(value, ensure_ascii=False)
                else:
                    flattened[key] = value
            flattened_records.append(flattened)
        
        return pd.DataFrame(flattened_records)
    
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {e}")
    except Exception as e:
        raise ValueError(f"Failed to read JSON file: {e}")


async def read_jsonl_file(file: UploadFile) -> pd.DataFrame:
    """
    Read JSONL (JSON Lines) file and convert to DataFrame.
    Each line is an independent JSON object.
    
    Features:
    - Memory efficient for large files
    - Handles nested JSON fields (converts to strings)
    - Skips empty lines and invalid JSON
    - Supports variable fields per record
    """
    try:
        content = await file.read()
        lines = content.decode('utf-8').strip().split('\n')
        
        records = []
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            
            try:
                record = json.loads(line)
                
                # Flatten nested JSON fields
                flattened = {}
                for key, value in record.items():
                    if isinstance(value, (dict, list)):
                        # Convert nested structures to JSON string
                        flattened[key] = json.dumps(value, ensure_ascii=False)
                    else:
                        flattened[key] = value
                
                records.append(flattened)
            
            except json.JSONDecodeError as e:
                print(f"[WARN] Line {i} is not valid JSON, skipping: {e}")
                continue
        
        if not records:
            raise ValueError("No valid JSON records found in JSONL file")
        
        return pd.DataFrame(records)
    
    except Exception as e:
        raise ValueError(f"Failed to read JSONL file: {e}")


def apply_question_prefix(question: str, prefix: Optional[str]) -> str:
    """
    Apply prefix to question if provided.
    Automatically adds appropriate separator.
    """
    if not prefix or not prefix.strip():
        return question
    
    prefix = prefix.strip()
    question = question.strip()
    
    # Use newline separator for long prefixes (>50 chars)
    # Use space for short prefixes
    separator = "\n\n" if len(prefix) > 50 else " "
    
    return f"{prefix}{separator}{question}"


# ============================================================
# Judge-Only mode (Excel/JSON/JSONL with model answers)
# ============================================================

@app.post("/api/judge-only-excel")
async def judge_only_excel(
    file: UploadFile = File(...),
    scenario: str = Form(...),
    dimensions: str = Form(...),
    mapping: str = Form(...),
    file_type: str = Form("excel"),  # New: excel | json | jsonl
    question_prefix: Optional[str] = Form(None),  # New: prefix for question
    expected_run_count: Optional[int] = Form(None),
    eval_session_id: Optional[str] = Form(None),  # New: abort support
):
    """
    Execute Judge evaluation on uploaded file (Excel/JSON/JSONL).
    Each entry (each row) independently calls Judge model once, results archived to eval_archive.xlsx.
    
    Supported file types:
    - excel: .xlsx, .xls
    - json: .json (array or {"data": array})
    - jsonl: .jsonl (JSON Lines, one JSON per line)
    
    New features:
    - question_prefix: Optional prefix to prepend to each question
    - Nested JSON fields in JSONL are automatically flattened to strings
    """
    # Parse parameters
    try:
        dims = json.loads(dimensions)
        col_map = json.loads(mapping)
        print(f"[DEBUG] File type: {file_type}")
        print(f"[DEBUG] Mapping parsed: {col_map}")
        print(f"[DEBUG] Question prefix: {question_prefix or '(none)'}")
        
        question_col = col_map.get("question")
        model_cols = col_map.get("models", [])
        image_col = col_map.get("image")  # Optional
        runtime_col = col_map.get("runtime")  # Optional
        model_asr_map = col_map.get("model_asr_fields", {})  # e.g. {"translation_e2e": "asr_e2e"}
        
        if not question_col or not model_cols:
            raise ValueError("mapping missing question or models field")
            
    except Exception as e:
        print(f"[ERROR] Parameter parse failed: {e}")
        return {"error": f"Parameter parse failed: {str(e)}", "status": "failed"}
    
    # Read file based on type
    try:
        if file_type == "excel":
            contents = await file.read()
            df = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
            print(f"[INFO] Loaded Excel file with {len(df)} rows")
        
        elif file_type == "json":
            df = await read_json_file(file)
            print(f"[INFO] Loaded JSON file with {len(df)} records")
        
        elif file_type == "jsonl":
            df = await read_jsonl_file(file)
            print(f"[INFO] Loaded JSONL file with {len(df)} records")
        
        else:
            return {"error": f"Unsupported file type: {file_type}", "status": "failed"}
        
        # Normalize column names (strip spaces)
        df.columns = [str(c).strip() for c in df.columns]
        
        print(f"[DEBUG] Available fields/columns: {df.columns.tolist()}")
        
    except ValueError as e:
        return {"error": str(e), "status": "failed"}
    except Exception as e:
        return {"error": f"File read failed: {str(e)}", "status": "failed"}
        
    # Column validation
    missing_cols = []
    for col in [question_col] + model_cols + ([image_col] if image_col else []) + ([runtime_col] if runtime_col else []):
        if col and col not in df.columns:
            missing_cols.append(col)
    if missing_cols:
        return {
            "error": f"Following columns don't exist in Excel: {missing_cols} (available columns: {df.columns.tolist()})",
            "status": "failed",
        }

    # Determine Run list
    unique_runs = sorted(df[runtime_col].dropna().unique()) if runtime_col else [1]

    if expected_run_count is not None and len(unique_runs) != expected_run_count:
        hint = " (Forgot to map runtime column?)" if not runtime_col and expected_run_count > 1 else ""
        return {
            "error": f"Run count mismatch: expected {expected_run_count}, actual {len(unique_runs)}{hint}",
            "status": "failed",
        }

    runs_data: List[Dict[str, Any]] = []
    trend_history: List[Dict[str, Any]] = []
    all_row_details: List[Dict[str, Any]] = []

    # Judge concurrency control (reuse global Semaphore)
    sem = get_judge_semaphore()

    async def run_task_with_sem(
        s: str,
        d: List,
        r: List,
        i: int,
        t: str,
    ) -> Dict[str, Any]:
        # Check abort flag before starting each task
        if eval_session_id and is_eval_aborted(eval_session_id):
            return {"row": i, "question": t, "error": "Evaluation aborted by user", "evaluations": {}, "answers": {}, "latencies": {}}
        async with sem:
            # Check again after acquiring semaphore
            if eval_session_id and is_eval_aborted(eval_session_id):
                return {"row": i, "question": t, "error": "Evaluation aborted by user", "evaluations": {}, "answers": {}, "latencies": {}}
            return await process_evaluation_task(s, d, r, i, t)

    # Iterate by Run
    for run_id in unique_runs:
        # Check abort before starting each run
        if eval_session_id and is_eval_aborted(eval_session_id):
            print(f"[INFO] Evaluation aborted before Run {run_id}", flush=True)
            break

        print(f"[INFO] Processing Run {run_id}...")
        run_df = df[df[runtime_col] == run_id].copy() if runtime_col else df.copy()

        tasks = []
        row_asr_data = {}  # {idx: models_asr} for injecting into results
        for idx, row in run_df.iterrows():
            raw_text = str(row[question_col])
            if pd.isna(raw_text) or not raw_text.strip():
                continue

            # Apply question prefix if provided
            text = apply_question_prefix(raw_text, question_prefix)

            row_test_results = []
            for m_col in model_cols:
                ans = row[m_col]
                ans = "No Answer" if pd.isna(ans) else str(ans)

                input_ctx = text
                if image_col:
                    img_val = row.get(image_col, "")
                    if not pd.isna(img_val) and str(img_val).strip():
                        input_ctx += f"\n[Image: {img_val}]"

                row_test_results.append({
                    "product_id": m_col,  # judge-only mode uses column name as product identifier
                    "answer": ans,
                    "input": input_ctx,
                })

            # Extract per-model ASR text (multi-model alignment scenario)
            if model_asr_map:
                row_models_asr = {}
                for m_col, asr_col in model_asr_map.items():
                    if asr_col in run_df.columns:
                        asr_val = row.get(asr_col, "")
                        if pd.notna(asr_val):
                            asr_str = str(asr_val).strip()
                            if asr_str and asr_str != "nan":
                                row_models_asr[m_col] = asr_str
                if row_models_asr:
                    row_asr_data[idx] = row_models_asr

            tasks.append(run_task_with_sem(scenario, dims, row_test_results, idx, text))

        print(f"[INFO] Run {run_id}: total {len(tasks)} entries, starting concurrent evaluation...")
        results_list = await asyncio.gather(*tasks)

        # Inject models_asr into results
        for result in results_list:
            row_idx = result.get("row")
            if row_idx in row_asr_data:
                result["models_asr"] = row_asr_data[row_idx]

        run_row_details = sorted(results_list, key=lambda x: x["row"])

        # Aggregate current Run
        run_aggregated: Dict[str, Any] = {}
        history_point: Dict[str, Any] = {"run": int(run_id) if str(run_id).isdigit() else run_id}

        for pid in model_cols:
            pid_runs = []
            for row in run_row_details:
                evals = row.get("evaluations", {})
                eval_data = evals.get(pid, {}).copy() if evals else {}
                eval_data["answer"] = row.get("answers", {}).get(pid, "No Answer")
                eval_data["latency"] = row.get("latencies", {}).get(pid, 0)
                eval_data["question"] = row.get("question", "")
                if not eval_data.get("scores"):
                    eval_data["scores"] = {}
                    eval_data["reasoning"] = row.get("error", "Failed or Missing")
                pid_runs.append(eval_data)

            avg_scores: Dict[str, Any] = {}
            for d in dims:
                dname = d["name"]
                dtype = d.get("type", "scale")
                valid_scores = [r["scores"].get(dname) for r in pid_runs if r.get("scores", {}).get(dname) is not None]

                if not valid_scores:
                    avg_scores[dname] = 0
                    continue

                if dtype == "scale":
                    try:
                        vals = [float(v) for v in valid_scores if str(v).replace(".", "", 1).lstrip("-").isdigit()]
                        avg_scores[dname] = round(sum(vals) / len(vals), 1) if vals else 0
                    except Exception:
                        avg_scores[dname] = 0
                elif dtype == "binary":
                    true_count = sum(1 for v in valid_scores if str(v).lower() in ["true", "1", "yes"])
                    avg_scores[dname] = round(true_count / len(valid_scores), 2)
                else:
                    avg_scores[dname] = 0  # categorical not participating in weighted calculation

            # Calculate weighted composite score (for trend chart)
            total_score = sum(
                (avg_scores.get(d["name"], 0) * (10 if d.get("type") == "binary" else 1)) * d.get("weight", 1)
                for d in dims if d.get("type") != "categorical"
            )
            max_score = sum(10 * d.get("weight", 1) for d in dims if d.get("type") != "categorical")
            history_point[pid] = round((total_score / max_score * 10) if max_score > 0 else 0, 1)

            run_aggregated[pid] = {
                "avgScores": avg_scores,
                "runs": pid_runs,
                "sampleReasoning": pid_runs[-1].get("reasoning", "") if pid_runs else "",
                "sampleAnswer": pid_runs[-1].get("answer", "") if pid_runs else "",
                "allAnswers": [r.get("answer", "") for r in pid_runs],
                "allLatencies": [r.get("latency", 0) for r in pid_runs],
                "allQuestions": [r.get("question", "") for r in pid_runs],
            }

        runs_data.append(run_aggregated)
        trend_history.append(history_point)
        all_row_details.extend(run_row_details)

    # Global aggregation (multi-Run average)
    global_aggregated: Dict[str, Any] = {}

    for pid in model_cols:
        all_run_avgs = [r[pid]["avgScores"] for r in runs_data]
        grand_avg: Dict[str, Any] = {}
        distribution: Dict[str, Any] = {}

        for d in dims:
            dname = d["name"]
            vals = [avg.get(dname, 0) for avg in all_run_avgs if isinstance(avg.get(dname), (int, float))]
            grand_avg[dname] = round(sum(vals) / len(vals), 1) if vals else 0

            if d.get("type") == "categorical":
                raw_vals = [
                    r["scores"].get(dname)
                    for run_agg in runs_data
                    for r in run_agg[pid]["runs"]
                    if r.get("scores", {}).get(dname) is not None
                ]
                counts: Dict[str, int] = {}
                for v in raw_vals:
                    counts[str(v)] = counts.get(str(v), 0) + 1
                distribution[dname] = counts

        last_run = runs_data[-1][pid]
        global_aggregated[pid] = {
            "avgScores": grand_avg,
            "distribution": distribution,
            "runs": last_run["runs"],
            "sampleReasoning": last_run["sampleReasoning"],
            "sampleAnswer": last_run["sampleAnswer"],
            "allAnswers": last_run["allAnswers"],
            "allLatencies": last_run["allLatencies"],
            "allQuestions": last_run["allQuestions"],
        }

    # Advanced analysis
    advanced_stats: Dict[str, Any] = {"entropy_weights": {}, "pca_scores": {}}
    try:
        valid_numeric_dims = [d["name"] for d in dims if d.get("type", "scale") in ["scale", "binary"]]
        data_for_math = []
        rows_index = []

        for pid in model_cols:
            avg_scores = global_aggregated[pid]["avgScores"]
            row_data = {
                dname: avg_scores[dname]
                for dname in valid_numeric_dims
                if isinstance(avg_scores.get(dname), (int, float))
            }
            if row_data:
                data_for_math.append(row_data)
                rows_index.append(pid)

        if data_for_math:
            df_math = pd.DataFrame(data_for_math, index=rows_index).fillna(0)
            advanced_stats["entropy_weights"] = calculate_entropy_weights(df_math)
            advanced_stats["pca_scores"] = calculate_pca_score(df_math)

    except Exception as e:
        print(f"[ERROR] Judge-Only advanced analysis failed: {e}\n{traceback.format_exc()}")

    # Check if evaluation was aborted
    was_aborted = eval_session_id and is_eval_aborted(eval_session_id)
    
    # Cleanup eval session
    if eval_session_id:
        cleanup_eval_session(eval_session_id)

    # ── Persist results to eval_results/ for history access ──
    run_id = None
    try:
        run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"judge_{run_ts}_{len(all_row_details)}r"
        run_dir = os.path.join(_BASE_DIR, "..", "eval_results", run_id)
        os.makedirs(run_dir, exist_ok=True)
        
        # Save row-level details as JSONL
        with open(os.path.join(run_dir, "results.jsonl"), "w", encoding="utf-8") as f:
            for row_detail in all_row_details:
                f.write(json.dumps(row_detail, ensure_ascii=False) + "\n")
        
        # Save summary.json (compatible with ZIP project format)
        summary = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "eval_type": "judge-only",
            "project_summary": {
                "total_files": 1,
                "success_files": 1 if not was_aborted else 0,
                "failed_files": 0,
                "total_rows_evaluated": len(all_row_details),
            },
            "eval_config_used": {
                "scenario": scenario,
                "mapping": json.loads(mapping),
                "dimensions": [d["name"] for d in dims],
                "file_type": file_type,
                "api_version": "v1",
            },
            "file_results": [{
                "file_id": "judge_only",
                "status": "success",
                "row_count": len(all_row_details),
                "results": global_aggregated,
            }],
            "dimensions_full": dims,
            "results": global_aggregated,
            "runs_data": runs_data,
            "trend_history": trend_history,
            "advanced_stats": advanced_stats,
        }
        with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        
        print(f"[INFO] Judge-Only results saved to eval_results/{run_id}", flush=True)
    except Exception as e:
        print(f"[WARN] Failed to save results to eval_results/: {e}", flush=True)

    return {
        "status": "aborted" if was_aborted else "success",
        "results": global_aggregated,
        "runs_data": runs_data,
        "trend_history": trend_history,
        "advanced_stats": advanced_stats,
        "aborted": was_aborted,
        "run_id": run_id,
    }


# ============================================================
# Field detection API for JSON/JSONL files
# ============================================================

@app.post("/api/detect-fields")
async def detect_fields(
    file: UploadFile = File(...),
    file_type: str = Form(...),
):
    """
    Detect available fields in JSON/JSONL file.
    Returns list of field names from the first record.
    
    Args:
        file: Uploaded JSON/JSONL file
        file_type: "json" or "jsonl"
    
    Returns:
        {
            "status": "success",
            "fields": ["field1", "field2", ...],
            "sample_record": {...}  # First record for preview
        }
    """
    try:
        if file_type == "json":
            df = await read_json_file(file)
        elif file_type == "jsonl":
            df = await read_jsonl_file(file)
        else:
            return {
                "error": f"Unsupported file type: {file_type}",
                "status": "failed"
            }
        
        if df.empty:
            return {
                "error": "File contains no records",
                "status": "failed"
            }
        
        # Get field names
        fields = df.columns.tolist()
        
        # Get first record as sample, replace NaN with None
        sample_record = df.iloc[0].replace({np.nan: None}).to_dict()
        
        # Truncate long values in sample for preview
        preview_sample = {}
        for key, value in sample_record.items():
            if value is None:
                preview_sample[key] = None
            elif isinstance(value, str) and len(value) > 200:
                preview_sample[key] = value[:200] + "..."
            else:
                preview_sample[key] = value
        
        print(f"[INFO] Detected {len(fields)} fields in {file_type} file")
        
        return {
            "status": "success",
            "fields": fields,
            "sample_record": preview_sample,
            "record_count": len(df)
        }
    
    except ValueError as e:
        return {"error": str(e), "status": "failed"}
    except Exception as e:
        print(f"[ERROR] Field detection failed: {e}\n{traceback.format_exc()}")
        return {"error": f"Failed to detect fields: {str(e)}", "status": "failed"}


# Manual evaluation API (frontend single call)
@app.post("/api/evaluate")
async def evaluate_results(request: EvaluationRequest):
    """Call Judge evaluation for single manual test result"""
    return await process_evaluation(
        request.scenario,
        request.dimensions,
        request.results,
        question=request.question,
        row_idx=0,
    )

# OpenRouter calling
async def call_openrouter(
    url: str,
    key: str,
    text: str,
    image_inputs: Optional[List[str]],
    model: str,
) -> Dict[str, Any]:
    """Call OpenRouter-compatible interface"""
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "EvalMatrix",
    }

    messages_content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
    if image_inputs:
        for img in image_inputs:
            messages_content.append({"type": "image_url", "image_url": {"url": img}})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": messages_content}],
    }

    max_retries = 3
    base_delay = 2.0

    for attempt in range(max_retries):
        start_time = time.time()
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(url, headers=headers, json=payload, timeout=60),
            )

            if response.status_code == 429:
                wait = base_delay * (2 ** attempt)
                print(f"[WARN] OpenRouter rate limit (429), retry in {wait}s...")
                await asyncio.sleep(wait)
                continue

            response.raise_for_status()
            data = response.json()
            latency = round((time.time() - start_time) * 1000, 2)

            try:
                answer = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                answer = json.dumps(data)

            return {"status": "success", "answer": answer, "raw": data, "latency": latency}

        except requests.exceptions.Timeout:
            print(f"[WARN] OpenRouter request timeout (attempt {attempt + 1})")
            if attempt < max_retries - 1:
                await asyncio.sleep(base_delay)
            else:
                return {"error": "Request timeout", "status": "failed", "latency": 0}

        except Exception as e:
            print(f"[ERROR] OpenRouter call failed (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(base_delay)
            else:
                latency = round((time.time() - start_time) * 1000, 2)
                return {"error": str(e), "status": "failed", "raw": str(e), "latency": latency}

    return {"error": "Exceeded max retries", "status": "failed", "latency": 0}

# Dify Workflow calling
async def call_dify(
    url: str,
    key: str,
    text: str,
    image_input: str,
) -> Dict[str, Any]:
    """Call Dify Workflow interface"""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    file_obj = None
    if image_input and image_input.startswith("http"):
        file_obj = {"type": "image", "transfer_method": "remote_url", "url": image_input}
    elif image_input and image_input.startswith("data:"):
        try:
            base_url = url.split("/workflows/run")[0]
            upload_url = f"{base_url}/files/upload"
            header, encoded = image_input.split(",", 1)
            mime_type = header.split(":")[1].split(";")[0]
            ext = mime_type.split("/")[1]
            file_data = base64.b64decode(encoded)
            upload_id = await upload_to_dify(upload_url, key, file_data, f"upload.{ext}", mime_type)
            if upload_id:
                file_obj = {"type": "image", "transfer_method": "local_file", "upload_file_id": upload_id}
        except Exception as e:
            print(f"[WARN] Dify image upload failed: {e}")

    inputs: Dict[str, Any] = {"text": text}
    if file_obj:
        inputs["fileinputbydondon"] = file_obj

    payload = {"inputs": inputs, "response_mode": "blocking", "user": "ai-tester-user"}

    start_time = time.time()
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        latency = round((time.time() - start_time) * 1000, 2)

        if not response.ok:
            return {"error": response.text, "status_code": response.status_code, "latency": latency}

        data = response.json()
        outputs = data.get("data", {}).get("outputs", {})

        # Extract output fields by priority
        answer = (
            outputs.get("text")
            or outputs.get("answer")
            or outputs.get("result")
            or (list(outputs.values())[0] if len(outputs) == 1 else json.dumps(outputs))
        )

        return {"status": "success", "answer": answer, "latency": latency, "raw": data}

    except Exception as e:
        latency = round((time.time() - start_time) * 1000, 2)
        print(f"[ERROR] Dify call failed: {e}\n{traceback.format_exc()}")
        return {"error": str(e), "status": "failed", "raw": str(e), "latency": latency}

# Dify file upload
async def upload_to_dify(
    url: str,
    key: str,
    file_data: bytes,
    filename: str,
    mime_type: str,
) -> Optional[str]:
    """Upload file to Dify, return upload_file_id; return None on failure"""
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (filename, file_data, mime_type)},
            data={"user": "ai-tester-user"},
            timeout=30,
        )
        if response.ok:
            return response.json().get("id")
        print(f"[WARN] Dify file upload failed (HTTP {response.status_code}): {response.text}")
        return None
    except Exception as e:
        print(f"[ERROR] Dify file upload exception: {e}")
        return None

# ============================================================
# 评测任务中断API
# ============================================================
@app.post("/api/eval/create-session")
async def create_eval_session_endpoint():
    """Create a new evaluation session for abort tracking"""
    sid = create_eval_session()
    return {"status": "success", "session_id": sid}

@app.post("/api/eval/abort/{session_id}")
async def abort_evaluation(session_id: str):
    """Abort a running evaluation session"""
    if abort_eval_session(session_id):
        return {"status": "success", "message": f"Evaluation {session_id} abort requested"}
    return {"status": "failed", "error": f"Session {session_id} not found or already completed"}

@app.get("/api/eval/sessions")
async def list_eval_sessions():
    """List active evaluation sessions"""
    active = {k: v for k, v in _eval_abort_flags.items() if not v}
    return {"sessions": list(active.keys()), "count": len(active)}

# ============================================================
# 历史评测结果API（读取 + 更新分数）
# ============================================================
EVAL_RESULTS_DIR = os.path.join(_BASE_DIR, "..", "eval_results")

@app.get("/api/eval-history")
async def list_eval_history(limit: int = 50):
    """List all historical evaluation runs (both ZIP project and judge-only)"""
    runs = []
    if os.path.exists(EVAL_RESULTS_DIR):
        for d in sorted(os.listdir(EVAL_RESULTS_DIR), reverse=True)[:limit]:
            sp = os.path.join(EVAL_RESULTS_DIR, d, "summary.json")
            if os.path.exists(sp):
                try:
                    with open(sp, "r", encoding="utf-8") as f:
                        s = json.load(f)
                    runs.append({
                        "run_id": d,
                        "timestamp": s.get("timestamp", ""),
                        "eval_type": s.get("eval_type", "zip-project"),
                        "summary": s.get("project_summary", {}),
                        "dimensions": s.get("eval_config_used", {}).get("dimensions", []),
                        "scenario": s.get("eval_config_used", {}).get("scenario", "")[:100],
                    })
                except Exception as e:
                    print(f"[WARN] Failed to read {sp}: {e}")
    return {"runs": runs}

@app.get("/api/eval-history/{run_id}")
async def get_eval_history_detail(run_id: str):
    """Load full evaluation results for a specific run, normalized for frontend consumption."""
    run_dir = os.path.join(EVAL_RESULTS_DIR, run_id)
    if not os.path.isdir(run_dir):
        return {"error": f"Run {run_id} not found", "status": "failed"}
    
    sp = os.path.join(run_dir, "summary.json")
    if not os.path.exists(sp):
        return {"error": "summary.json not found", "status": "failed"}
    
    with open(sp, "r", encoding="utf-8") as f:
        summary = json.load(f)
    
    # Load row-level results from JSONL files
    row_details = []
    for fname in sorted(os.listdir(run_dir)):
        if fname.endswith("_results.jsonl") or fname == "results.jsonl":
            fpath = os.path.join(run_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            row_details.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
    
    row_details.sort(key=lambda x: x.get("row", 0))
    
    # ── Normalize: ensure top-level `results` and `runs_data` exist ──
    # ZIP project format: results live inside file_results[*].results
    # Judge-only format: results are at summary top-level
    
    results = summary.get("results")
    runs_data = summary.get("runs_data")
    dims_full = summary.get("dimensions_full")
    
    if not results and summary.get("file_results"):
        # Merge file_results into a single results dict (like the frontend ZIP merge logic)
        merged: Dict[str, Any] = {}
        for fr in summary["file_results"]:
            if fr.get("status") != "success" or not fr.get("results"):
                continue
            for model_name, model_data in fr["results"].items():
                if model_name not in merged:
                    merged[model_name] = {
                        "avgScores": {},
                        "runs": [],
                        "sampleReasoning": "",
                        "sampleAnswer": "",
                        "allAnswers": [],
                        "allQuestions": [],
                        "allLatencies": [],
                    }
                m = merged[model_name]
                # Inject row index into runs for human review tracking
                base_idx = len(m["runs"])
                incoming_runs = model_data.get("runs", [])
                for ri, run_item in enumerate(incoming_runs):
                    if "_row_idx" not in run_item:
                        run_item["_row_idx"] = base_idx + ri
                m["runs"].extend(incoming_runs)
                m["allAnswers"].extend(model_data.get("allAnswers", []))
                m["allQuestions"].extend(model_data.get("allQuestions", []))
                m["allLatencies"].extend(model_data.get("allLatencies", []))
                m["sampleReasoning"] = model_data.get("sampleReasoning") or m["sampleReasoning"]
                m["sampleAnswer"] = model_data.get("sampleAnswer") or m["sampleAnswer"]
        
        # Recompute averages from merged runs
        dim_names = summary.get("eval_config_used", {}).get("dimensions", [])
        for model_name, model_data in merged.items():
            avg_scores: Dict[str, Any] = {}
            all_runs = model_data.get("runs", [])
            if all_runs:
                for dk in dim_names:
                    vals = []
                    for r in all_runs:
                        v = r.get("scores", {}).get(dk)
                        if v is not None:
                            try:
                                vals.append(float(v))
                            except (ValueError, TypeError):
                                pass
                    avg_scores[dk] = round(sum(vals) / len(vals), 1) if vals else 0
            model_data["avgScores"] = avg_scores
        
        results = merged
        runs_data = [merged]
    
    if not runs_data and results:
        runs_data = [results]
    
    # ── Normalize dimensions_full ──
    if not dims_full:
        dim_names = summary.get("eval_config_used", {}).get("dimensions", [])
        
        # Infer dimension types from actual score values in results
        dim_type_map: Dict[str, str] = {}
        dim_options_map: Dict[str, set] = {}
        if results:
            for model_name, model_data in results.items():
                for run_item in model_data.get("runs", [])[:50]:  # sample up to 50 rows
                    scores = run_item.get("scores", {})
                    for dk in dim_names:
                        v = scores.get(dk)
                        if v is None:
                            continue
                        if dk not in dim_type_map:
                            dim_type_map[dk] = "unknown"
                            dim_options_map[dk] = set()
                        
                        sv = str(v).lower().strip()
                        
                        # Check if binary (true/false/yes/no/0/1)
                        if sv in ("true", "false", "yes", "no"):
                            dim_type_map[dk] = "binary"
                        elif isinstance(v, bool):
                            dim_type_map[dk] = "binary"
                        elif isinstance(v, str) and not sv.replace(".", "", 1).replace("-", "", 1).isdigit():
                            # Non-numeric string → categorical
                            dim_type_map[dk] = "categorical"
                            dim_options_map[dk].add(str(v))
                        else:
                            # Numeric → scale (but don't override binary/categorical)
                            if dim_type_map[dk] == "unknown":
                                dim_type_map[dk] = "scale"
        
        dims_full = []
        for n in dim_names:
            inferred_type = dim_type_map.get(n, "scale")
            if inferred_type == "unknown":
                inferred_type = "scale"
            dim_entry: Dict[str, Any] = {"name": n, "description": "", "weight": 1, "type": inferred_type}
            if inferred_type == "categorical" and dim_options_map.get(n):
                dim_entry["options"] = sorted(dim_options_map[n])
            dims_full.append(dim_entry)
    
    # ── Build trend_history if not present ──
    trend_history = summary.get("trend_history", [])
    advanced_stats = summary.get("advanced_stats", {})
    
    return {
        "status": "success",
        "summary": summary,
        "results": results or {},
        "runs_data": runs_data or [],
        "dimensions_full": dims_full or [],
        "trend_history": trend_history,
        "advanced_stats": advanced_stats,
        "row_details": row_details,
        "total_rows": len(row_details),
    }

class ScoreUpdate(BaseModel):
    run_id: str
    updates: List[Dict[str, Any]]  # [{row: int, model: str, scores: {...}, file_id?: str}]

@app.post("/api/eval-history/update-scores")
async def update_eval_scores(req: ScoreUpdate):
    """
    Update scores in historical eval results (from human review).
    Updates both the JSONL row files and summary.json.
    """
    run_dir = os.path.join(EVAL_RESULTS_DIR, req.run_id)
    if not os.path.isdir(run_dir):
        return {"error": f"Run {req.run_id} not found", "status": "failed"}
    
    # Build update index: {(file_id, row): {model: scores}}
    update_map: Dict[tuple, Dict[str, Dict]] = {}
    for u in req.updates:
        key = (u.get("file_id", ""), u.get("row", 0))
        if key not in update_map:
            update_map[key] = {}
        update_map[key][u["model"]] = u["scores"]
    
    updated_count = 0
    
    # Process each JSONL file in the run directory
    for fname in os.listdir(run_dir):
        if not (fname.endswith("_results.jsonl") or fname == "results.jsonl"):
            continue
        
        fpath = os.path.join(run_dir, fname)
        
        # Determine file_id from filename
        file_id = ""
        if fname == "results.jsonl":
            file_id = ""
        else:
            file_id = fname.replace("_results.jsonl", "")
        
        # Read all lines
        lines = []
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        modified = False
        new_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                new_lines.append(line)
                continue
            
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                new_lines.append(line)
                continue
            
            row_num = record.get("row", -1)
            key = (file_id, row_num)
            
            if key in update_map:
                # Apply score updates
                for model_name, new_scores in update_map[key].items():
                    if "evaluations" in record and model_name in record["evaluations"]:
                        old_scores = record["evaluations"][model_name].get("scores", {})
                        old_scores.update(new_scores)
                        record["evaluations"][model_name]["scores"] = old_scores
                        record["evaluations"][model_name]["human_reviewed"] = True
                        modified = True
                        updated_count += 1
                
                new_lines.append(json.dumps(record, ensure_ascii=False))
            else:
                new_lines.append(line)
        
        if modified:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")
    
    # Rebuild summary.json if scores changed
    if updated_count > 0:
        try:
            await _rebuild_summary(req.run_id)
        except Exception as e:
            print(f"[WARN] Failed to rebuild summary for {req.run_id}: {e}")
    
    return {
        "status": "success",
        "updated_count": updated_count,
        "run_id": req.run_id,
    }

async def _rebuild_summary(run_id: str):
    """Rebuild summary.json from JSONL result files after score updates"""
    run_dir = os.path.join(EVAL_RESULTS_DIR, run_id)
    sp = os.path.join(run_dir, "summary.json")
    
    if not os.path.exists(sp):
        return
    
    with open(sp, "r", encoding="utf-8") as f:
        summary = json.load(f)
    
    dims_config = summary.get("dimensions_full", [])
    if not dims_config:
        # Try to reconstruct from dimension names
        dim_names = summary.get("eval_config_used", {}).get("dimensions", [])
        dims_config = [{"name": n, "type": "scale", "weight": 1} for n in dim_names]
    
    # Re-read all results and re-aggregate
    all_rows = []
    for fname in sorted(os.listdir(run_dir)):
        if fname.endswith("_results.jsonl") or fname == "results.jsonl":
            fpath = os.path.join(run_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            all_rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
    
    if not all_rows:
        return
    
    # Detect model names from evaluations
    model_names = set()
    for row in all_rows:
        for m in row.get("evaluations", {}).keys():
            model_names.add(m)
    
    # Re-aggregate per model
    new_results = {}
    for model_name in model_names:
        runs_list = []
        for row in all_rows:
            evals = row.get("evaluations", {})
            eval_data = evals.get(model_name, {}).copy() if evals else {}
            eval_data["answer"] = row.get("answers", {}).get(model_name, "")
            eval_data["question"] = row.get("question", "")
            if not eval_data.get("scores"):
                eval_data["scores"] = {}
                eval_data["reasoning"] = row.get("error", "Failed or Missing")
            runs_list.append(eval_data)
        
        # Compute averages
        avg_scores = {}
        for d in dims_config:
            dname = d["name"]
            dtype = d.get("type", "scale")
            valid = [r["scores"].get(dname) for r in runs_list if r.get("scores", {}).get(dname) is not None]
            if not valid:
                avg_scores[dname] = 0
                continue
            if dtype == "scale":
                vals = []
                for v in valid:
                    try:
                        vals.append(float(v))
                    except (ValueError, TypeError):
                        pass
                avg_scores[dname] = round(sum(vals) / len(vals), 1) if vals else 0
            elif dtype == "binary":
                true_count = sum(1 for v in valid if str(v).lower() in ["true", "1", "yes"])
                avg_scores[dname] = round(true_count / len(valid), 2)
            else:
                avg_scores[dname] = 0
        
        new_results[model_name] = {
            "avgScores": avg_scores,
            "runs": runs_list,
            "sampleReasoning": runs_list[-1].get("reasoning", "") if runs_list else "",
            "sampleAnswer": runs_list[-1].get("answer", "") if runs_list else "",
            "allAnswers": [r.get("answer", "") for r in runs_list],
            "allQuestions": [r.get("question", "") for r in runs_list],
        }
    
    # Update summary
    summary["results"] = new_results
    summary["runs_data"] = [new_results]
    summary["last_updated"] = datetime.now().isoformat()
    
    # Update file_results if exists
    if summary.get("file_results"):
        for fr in summary["file_results"]:
            if fr.get("results"):
                for model_name in new_results:
                    if model_name in fr["results"]:
                        fr["results"][model_name]["avgScores"] = new_results[model_name]["avgScores"]
    
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"[INFO] Rebuilt summary for {run_id} after score update", flush=True)

# ============================================================
# 批量上下文评分API (Batch Context Judge v2)
# 端点: POST /api/judge-only-excel-batch
# 支持: file_context (文件级上下文) + context_fields (行级上下文)
# ============================================================
try:
    from judge_batch_context_api import register_batch_context_routes
    register_batch_context_routes(app)
except ImportError as e:
    print(f"[WARN] Batch context judge module not found: {e}", flush=True)
except Exception as e:
    print(f"[ERROR] Failed to register batch context judge API: {e}", flush=True)

# ============================================================
# 批量项目评测API (Batch Project)
# 端点: POST /api/judge-batch-project  - ZIP批量评测
#        POST /api/parse-manifest       - 预览ZIP配置
# ============================================================
try:
    from judge_batch_project_api import register_batch_project_routes
    register_batch_project_routes(app)
except ImportError as e:
    print(f"[WARN] Batch project module not found: {e}", flush=True)
except Exception as e:
    print(f"[ERROR] Failed to register batch project API: {e}", flush=True)

# ============================================================
# Batch API 异步批量任务端点
# 端点: POST /api/batch/submit        - 提交 Batch 任务
#        GET  /api/batch/{id}/status   - 查询任务状态
#        POST /api/batch/{id}/cancel   - 取消任务
#        GET  /api/batch/{id}/results  - 获取结果
#        GET  /api/batch/list          - 列出任务
# ============================================================
try:
    try:
        from batch_api_routes import register_batch_api_routes
    except ImportError:
        from backend.batch_api_routes import register_batch_api_routes
    register_batch_api_routes(app)
except ImportError as e:
    print(f"[WARN] Batch API routes module not found: {e}", flush=True)
except Exception as e:
    print(f"[ERROR] Failed to register Batch API routes: {e}", flush=True)

# ============================================================
# 多模型数据对齐API (Multi-Model Alignment)
# 端点: POST /api/alignment/upload      - 上传zip并对齐
#        GET  /api/alignment/status/:id  - 查询进度
#        GET  /api/alignment/preview/:id - 预览对齐结果
#        POST /api/alignment/confirm/:id - 确认并导入Judge
#        GET  /api/alignment/download/:id- 下载ZIP包
# ============================================================
try:
    try:
        from alignment_api_routes import register_alignment_routes
    except ImportError:
        from backend.alignment_api_routes import register_alignment_routes
    register_alignment_routes(app)
    print("[INFO] Alignment API registered successfully", flush=True)
except ImportError as e:
    print(f"[WARN] Alignment API not loaded: {e}", flush=True)
except Exception as e:
    print(f"[ERROR] Alignment API init failed: {e}", flush=True)

# Force reload trigger comment

