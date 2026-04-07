"""
LLM-assisted alignment tool v3 for ASR+Translation xlsx <-> transcript txt.

Core problem:
- xlsx contains ASR segments (col 1, 原文STT) + translations (col 2, 翻译)
- Each xlsx ASR row merges multiple txt lines (txt is finer-grained)
- ASR has recognition errors (同音字替换, missing chars, extra chars)
- xlsx may be truncated (only covers first portion of txt)
- Need to align each xlsx row to its corresponding txt line(s)

Approach:
1. Concatenate both sources into continuous strings
2. Use SequenceMatcher to build a character-level mapping from xlsx_concat -> txt_concat  
3. Use the mapping to find each xlsx segment's position in the txt
4. For low-confidence segments, call LLM for refinement
5. When xlsx is truncated, only align xlsx content (ignore extra txt)

Usage:
  python align_with_llm.py [--data-dir DIR] [--output DIR] [--llm-provider PROVIDER] [--dry-run]
"""

import os
import re
import sys
import json
import glob
import time
import argparse
import pandas as pd
from difflib import SequenceMatcher
from typing import List, Tuple, Dict, Optional


# ─── Configuration ───────────────────────────────────────────────────────────

DEFAULT_DATA_DIR = r"E:\同传\4-已核对待评测-ASR+翻译+TTS数据\小语种-已核对\es2en-14"
DEFAULT_OUTPUT_DIR = r'D:\POPO\AItester0227\AItester0227 - latest\aligned_output'

LLM_CONFIG = {
    'provider': 'openai',
    'api_key': os.environ.get('LLM_API_KEY', ''),  # 从环境变量读取，或使用 --llm-api-key 参数
    'api_base': os.environ.get('LLM_API_BASE', 'https://api.openai.com/v1'),
    'model': os.environ.get('LLM_MODEL', 'gpt-4'),
    'temperature': 0.1,
    'max_tokens': 1024,
}


# ─── Text Utilities ──────────────────────────────────────────────────────────

PUNCT_RE = re.compile(r'[\s、。！？「」（）().,!?\-\u3000\u3001\u3002\uff01\uff1f\uff0c\uff0e\u300c\u300d\u2026\u30fb\uff1a\uff1b:;\u200b]')

def clean_jp(s: str) -> str:
    """Remove Japanese/CJK punctuation and whitespace for comparison."""
    return PUNCT_RE.sub('', s)


def similarity(a: str, b: str) -> float:
    """Character-level similarity ratio between two cleaned strings."""
    ca, cb = clean_jp(a), clean_jp(b)
    if not ca or not cb:
        return 0.0
    return SequenceMatcher(None, ca, cb).ratio()


# ─── Core Alignment: Concatenation + Character Mapping ───────────────────────

def build_char_map(xlsx_asr: List[str], txt_lines: List[str]) -> List[Dict]:
    """
    Concatenate xlsx ASR and txt lines separately (with separators),
    build a character-level mapping, then determine which txt lines
    correspond to each xlsx row.
    
    Returns list of alignment dicts.
    """
    # Build concatenated strings with tracked segment boundaries
    # xlsx: join ASR segments with a separator char
    SEP = '\x00'  # null char as separator (won't appear in text)
    
    xlsx_clean_parts = [clean_jp(a) for a in xlsx_asr]
    txt_clean_parts = [clean_jp(l) for l in txt_lines]
    
    xlsx_concat = SEP.join(xlsx_clean_parts)
    txt_concat = SEP.join(txt_clean_parts)
    
    # Record segment boundaries in xlsx_concat
    xlsx_seg_ranges = []  # (start, end) in xlsx_concat for each xlsx row
    pos = 0
    for i, part in enumerate(xlsx_clean_parts):
        xlsx_seg_ranges.append((pos, pos + len(part)))
        pos += len(part) + 1  # +1 for separator
    
    # Record segment boundaries in txt_concat
    txt_seg_ranges = []  # (start, end) in txt_concat for each txt line
    pos = 0
    for i, part in enumerate(txt_clean_parts):
        txt_seg_ranges.append((pos, pos + len(part)))
        pos += len(part) + 1
    
    # SequenceMatcher on cleaned concatenated strings (without separators)
    xlsx_flat = ''.join(xlsx_clean_parts)
    txt_flat = ''.join(txt_clean_parts)
    
    # Build cumulative offset maps: position in flat -> position in concat (with seps)
    def flat_to_concat_map(parts):
        """Map flat-string positions to concat-with-sep positions."""
        mapping = []
        flat_pos = 0
        concat_pos = 0
        for i, part in enumerate(parts):
            for j in range(len(part)):
                mapping.append(concat_pos + j)
            flat_pos += len(part)
            concat_pos += len(part) + 1  # sep
        return mapping
    
    xlsx_f2c = flat_to_concat_map(xlsx_clean_parts)
    txt_f2c = flat_to_concat_map(txt_clean_parts)
    
    # Run SequenceMatcher on flat strings
    sm = SequenceMatcher(None, xlsx_flat, txt_flat, autojunk=False)
    
    # Build xlsx_flat_pos -> txt_flat_pos mapping
    x2t_flat = {}  # xlsx flat position -> txt flat position
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                x2t_flat[i1 + k] = j1 + k
        elif tag == 'replace':
            slen, tlen = i2 - i1, j2 - j1
            for k in range(slen):
                x2t_flat[i1 + k] = j1 + int(k * tlen / slen)
        elif tag == 'delete':
            for k in range(i2 - i1):
                x2t_flat[i1 + k] = j1  # map to start of gap
    
    # Now for each xlsx segment, find which txt lines it maps to
    alignments = []
    
    # Track xlsx flat position per segment
    xlsx_flat_pos = 0
    for xlsx_idx, (seg_start, seg_end) in enumerate(xlsx_seg_ranges):
        seg_len = seg_end - seg_start
        
        if seg_len == 0:
            # Empty ASR segment
            alignments.append({
                'xlsx_idx': xlsx_idx,
                'txt_line_start': -1,
                'txt_line_end': -1,
                'txt_content': '',
                'confidence': 0.0,
                'status': 'empty_asr',
            })
            xlsx_flat_pos += seg_len
            continue
        
        # Map this xlsx segment's flat positions to txt flat positions
        mapped_txt_positions = []
        for k in range(seg_len):
            flat_pos = xlsx_flat_pos + k
            if flat_pos in x2t_flat:
                mapped_txt_positions.append(x2t_flat[flat_pos])
        
        xlsx_flat_pos += seg_len
        
        if not mapped_txt_positions:
            alignments.append({
                'xlsx_idx': xlsx_idx,
                'txt_line_start': -1,
                'txt_line_end': -1,
                'txt_content': '',
                'confidence': 0.0,
                'status': 'no_mapping',
            })
            continue
        
        # Convert txt flat positions to txt concat positions, then to txt line indices
        txt_concat_positions = [txt_f2c[p] for p in mapped_txt_positions if p < len(txt_f2c)]
        
        if not txt_concat_positions:
            alignments.append({
                'xlsx_idx': xlsx_idx,
                'txt_line_start': -1,
                'txt_line_end': -1,
                'txt_content': '',
                'confidence': 0.0,
                'status': 'no_mapping',
            })
            continue
        
        # Find which txt segments these positions fall in
        min_pos = min(txt_concat_positions)
        max_pos = max(txt_concat_positions)
        
        txt_line_start = None
        txt_line_end = None
        for li, (ts, te) in enumerate(txt_seg_ranges):
            if ts <= min_pos < te + 1:
                txt_line_start = li
            if ts <= max_pos < te + 1:
                txt_line_end = li
        
        # Fallback: binary search
        if txt_line_start is None:
            for li, (ts, te) in enumerate(txt_seg_ranges):
                if te > min_pos:
                    txt_line_start = li
                    break
        if txt_line_end is None:
            for li, (ts, te) in enumerate(txt_seg_ranges):
                if te >= max_pos:
                    txt_line_end = li
                    break
        
        if txt_line_start is None:
            txt_line_start = 0
        if txt_line_end is None:
            txt_line_end = len(txt_lines) - 1
        
        matched_content = ' '.join(txt_lines[txt_line_start:txt_line_end + 1])
        conf = similarity(xlsx_asr[xlsx_idx], matched_content)
        
        alignments.append({
            'xlsx_idx': xlsx_idx,
            'txt_line_start': txt_line_start,
            'txt_line_end': txt_line_end,
            'txt_content': matched_content,
            'confidence': conf,
            'status': 'ok' if conf >= 0.5 else 'low_confidence',
        })
    
    return alignments


def fix_overlaps_and_gaps(alignments: List[Dict], txt_lines: List[str],
                          xlsx_asr: List[str]) -> List[Dict]:
    """
    Post-process alignments to fix overlapping and gap issues.
    Ensure monotonic txt line assignments and no overlaps.
    Preserves LLM-refined results when possible.
    """
    fixed = list(alignments)
    
    last_end = -1
    for i in range(len(fixed)):
        if fixed[i]['txt_line_start'] < 0:
            continue
        
        # Skip nan/empty ASR — don't let them consume txt lines
        asr_text = xlsx_asr[fixed[i]['xlsx_idx']]
        if not asr_text or str(asr_text).lower() == 'nan' or not clean_jp(str(asr_text)):
            fixed[i]['txt_line_start'] = -1
            fixed[i]['txt_line_end'] = -1
            fixed[i]['txt_content'] = ''
            fixed[i]['confidence'] = 0.0
            fixed[i]['status'] = 'empty_asr'
            continue
        
        # Ensure start is after previous end
        if fixed[i]['txt_line_start'] <= last_end:
            fixed[i]['txt_line_start'] = last_end + 1
        
        # Ensure end >= start
        if fixed[i]['txt_line_end'] < fixed[i]['txt_line_start']:
            fixed[i]['txt_line_end'] = fixed[i]['txt_line_start']
        
        # Clamp to valid range
        if fixed[i]['txt_line_start'] >= len(txt_lines):
            fixed[i]['txt_line_start'] = -1
            fixed[i]['txt_line_end'] = -1
            fixed[i]['txt_content'] = ''
            fixed[i]['confidence'] = 0.0
            fixed[i]['status'] = 'out_of_range'
            continue
        
        fixed[i]['txt_line_end'] = min(fixed[i]['txt_line_end'], len(txt_lines) - 1)
        
        last_end = fixed[i]['txt_line_end']
        
        # Recompute content and confidence
        matched = ' '.join(txt_lines[fixed[i]['txt_line_start']:fixed[i]['txt_line_end'] + 1])
        fixed[i]['txt_content'] = matched
        fixed[i]['confidence'] = similarity(str(asr_text), matched)
        if fixed[i]['confidence'] >= 0.5:
            fixed[i]['status'] = 'ok'
    
    return fixed


# ─── LLM Alignment (Pass 2) ─────────────────────────────────────────────────

def build_llm_prompt(xlsx_asr_segment: str, xlsx_trans: str,
                     candidate_txt_lines: List[str],
                     candidate_start_idx: int,
                     prev_end_line: int = -1) -> str:
    """Build prompt for LLM to determine alignment."""
    numbered_lines = '\n'.join(
        f'  [{candidate_start_idx + i}] {line}'
        for i, line in enumerate(candidate_txt_lines)
    )
    
    constraint = ''
    if prev_end_line >= 0:
        constraint = f'\n注意：前一段已对齐到行 {prev_end_line}，所以起始行号必须 > {prev_end_line}。'
    
    return f"""你是一个文本对齐专家。请帮我判断以下ASR识别文本对应原文的哪些行。

ASR识别结果（可能有识别错误、同音字替换、漏字）：
  "{xlsx_asr_segment}"

翻译参考（英文翻译）：
  "{xlsx_trans}"

候选原文行（带行号）：
{numbered_lines}
{constraint}
请找出ASR文本对应的原文行范围。一条ASR通常对应1-5行原文。

请以JSON格式回答：
{{"start_line": <起始行号>, "end_line": <结束行号>, "explanation": "<简短说明>"}}

只返回JSON，不要其他内容。"""


def call_llm(prompt: str, config: dict) -> Optional[str]:
    """Call LLM API via requests (OpenAI-compatible endpoint)."""
    import requests as _requests
    
    api_base = config.get('api_base', '').rstrip('/')
    url = f'{api_base}/chat/completions'
    
    headers = {'Content-Type': 'application/json'}
    if config.get('api_key'):
        headers['Authorization'] = f'Bearer {config["api_key"]}'
    
    payload = {
        'model': config['model'],
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': config.get('temperature', 0.1),
        'max_tokens': config.get('max_tokens', 1024),
        'stream': False,
    }
    
    try:
        resp = _requests.post(url, headers=headers, json=payload, timeout=60)
        
        # Handle rate limiting with retry
        if resp.status_code == 429:
            for retry in range(3):
                wait = (retry + 1) * 5  # 5s, 10s, 15s
                print(f'  [LLM] Rate limited, waiting {wait}s (retry {retry+1}/3)...')
                time.sleep(wait)
                resp = _requests.post(url, headers=headers, json=payload, timeout=60)
                if resp.status_code != 429:
                    break
        
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'  [LLM ERROR] {e}')
        return None


def parse_llm_response(response: str) -> Optional[Dict]:
    """Parse LLM JSON response."""
    if not response:
        return None
    try:
        match = re.search(r'\{[^}]+\}', response)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError:
        pass
    return None


def llm_refine_alignments(alignments: List[Dict], xlsx_asr: List[str],
                          xlsx_trans: List[str], txt_lines: List[str],
                          config: dict, threshold: float = 0.5) -> List[Dict]:
    """Use LLM to fix low-confidence alignments."""
    if not config.get('provider'):
        print('  [LLM] No provider configured, skipping')
        return alignments
    
    refined = list(alignments)
    
    for i, align in enumerate(refined):
        if align['confidence'] >= threshold:
            continue
        if align['status'] in ('empty_asr', 'no_txt_remaining'):
            continue
        
        # Skip nan/empty ASR
        asr_text = xlsx_asr[align['xlsx_idx']]
        if not asr_text or asr_text.lower() == 'nan' or not clean_jp(asr_text):
            continue
        
        # Determine candidate window
        prev_end = refined[i-1]['txt_line_end'] if i > 0 and refined[i-1]['txt_line_start'] >= 0 else -1
        next_start_hint = None
        for j in range(i+1, min(i+5, len(refined))):
            if refined[j]['txt_line_start'] >= 0 and refined[j]['confidence'] >= threshold:
                next_start_hint = refined[j]['txt_line_start']
                break
        
        window_start = max(0, prev_end + 1) if prev_end >= 0 else max(0, align.get('txt_line_start', 0) - 5)
        window_end = min(len(txt_lines), 
                        (next_start_hint + 3 if next_start_hint else window_start + 30))
        
        candidate_lines = txt_lines[window_start:window_end]
        if not candidate_lines:
            continue
        
        prompt = build_llm_prompt(
            asr_text,
            xlsx_trans[align['xlsx_idx']],
            candidate_lines,
            window_start,
            prev_end,
        )
        
        print(f'  [LLM] Refining xlsx[{align["xlsx_idx"]}] conf={align["confidence"]:.2f}...')
        response = call_llm(prompt, config)
        result = parse_llm_response(response)
        
        if result and 'start_line' in result and 'end_line' in result:
            start = int(result['start_line'])
            end = int(result['end_line'])
            
            if 0 <= start <= end < len(txt_lines) and start > prev_end:
                matched = ' '.join(txt_lines[start:end+1])
                new_conf = similarity(asr_text, matched)
                
                refined[i] = {
                    'xlsx_idx': align['xlsx_idx'],
                    'txt_line_start': start,
                    'txt_line_end': end,
                    'txt_content': matched,
                    'confidence': max(new_conf, 0.65),  # LLM gives minimum boost
                    'status': 'llm_refined',
                }
                print(f'    -> lines [{start}-{end}], conf={refined[i]["confidence"]:.2f}')
        
        # Rate limit: avoid hitting API too fast
        time.sleep(1.0)
    
    return refined


# ─── Output ──────────────────────────────────────────────────────────────────

def build_output_df(alignments: List[Dict], xlsx_asr: List[str],
                    xlsx_trans: List[str], df_xlsx: pd.DataFrame) -> pd.DataFrame:
    """Build output DataFrame."""
    rows = []
    for align in alignments:
        idx = align['xlsx_idx']
        row = {
            '段落ID': idx,
            'ASR识别(xlsx)': xlsx_asr[idx],
            '翻译(xlsx)': xlsx_trans[idx],
            '对应原文(txt)': align['txt_content'],
            '原文行号': f'{align["txt_line_start"]+1}-{align["txt_line_end"]+1}'
                       if align['txt_line_start'] >= 0 else 'N/A',
            '匹配置信度': f'{align["confidence"]:.2f}',
            '状态': align['status'],
        }
        # Preserve timing columns
        if df_xlsx.shape[1] > 3:
            data_row = idx + 1
            if data_row < len(df_xlsx):
                for col_idx in range(3, min(df_xlsx.shape[1], 10)):
                    col_name = str(df_xlsx.iloc[0, col_idx]) if pd.notna(df_xlsx.iloc[0, col_idx]) else f'col_{col_idx}'
                    row[col_name] = df_xlsx.iloc[data_row, col_idx]
        rows.append(row)
    return pd.DataFrame(rows)


# ─── Main Pipeline ───────────────────────────────────────────────────────────

def process_one_pair(xlsx_path: str, txt_path: str, output_path: str,
                     llm_config: dict = None, dry_run: bool = False) -> Dict:
    """Process one xlsx+txt pair."""
    stem = os.path.basename(xlsx_path).replace('__sentences.xlsx', '')
    print(f'\n{"="*80}')
    print(f'Processing: {stem[:70]}')
    
    df = pd.read_excel(xlsx_path, header=None)
    xlsx_asr = [str(x).strip() for x in df.iloc[1:, 1].tolist()]
    xlsx_trans = [str(x).strip() for x in df.iloc[1:, 2].tolist()]
    
    with open(txt_path, 'r', encoding='utf-8') as f:
        txt_lines = [l.strip() for l in f if l.strip()]
    
    print(f'  XLSX rows: {len(xlsx_asr)}, TXT lines: {len(txt_lines)}')
    
    # Check truncation
    xlsx_chars = sum(len(clean_jp(a)) for a in xlsx_asr)
    txt_chars = sum(len(clean_jp(l)) for l in txt_lines)
    coverage = xlsx_chars / max(txt_chars, 1)
    is_truncated = coverage < 0.85
    
    if is_truncated:
        print(f'  XLSX truncated: ~{coverage:.0%} coverage, aligning only xlsx content')
    
    # Pass 1: Character-mapping alignment
    print(f'  Pass 1: Character-level mapping...')
    alignments = build_char_map(xlsx_asr, txt_lines)
    alignments = fix_overlaps_and_gaps(alignments, txt_lines, xlsx_asr)
    
    low_conf = sum(1 for a in alignments if a['confidence'] < 0.5)
    avg_conf = sum(a['confidence'] for a in alignments) / max(len(alignments), 1)
    print(f'    Avg confidence: {avg_conf:.2f}, low-conf: {low_conf}/{len(alignments)}')
    
    # Pass 2: LLM refinement
    if llm_config and llm_config.get('provider') and low_conf > 0:
        print(f'  Pass 2: LLM refinement for {low_conf} items...')
        alignments = llm_refine_alignments(
            alignments, xlsx_asr, xlsx_trans, txt_lines, llm_config
        )
        # Re-fix after LLM changes
        alignments = fix_overlaps_and_gaps(alignments, txt_lines, xlsx_asr)
        low_conf = sum(1 for a in alignments if a['confidence'] < 0.5)
        avg_conf = sum(a['confidence'] for a in alignments) / max(len(alignments), 1)
        print(f'    After LLM: avg={avg_conf:.2f}, low-conf: {low_conf}/{len(alignments)}')
    
    # Output
    if not dry_run:
        out_df = build_output_df(alignments, xlsx_asr, xlsx_trans, df)
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        out_df.to_excel(output_path, index=False, engine='openpyxl')
        print(f'  Saved: {output_path}')
    
    stats = {
        'stem': stem,
        'xlsx_rows': len(xlsx_asr),
        'txt_lines': len(txt_lines),
        'truncated': is_truncated,
        'coverage': f'{coverage:.0%}',
        'aligned': len(alignments),
        'high_conf': sum(1 for a in alignments if a['confidence'] >= 0.5),
        'low_conf': low_conf,
        'avg_conf': round(avg_conf, 3),
    }
    print(f'  Result: high={stats["high_conf"]}, low={stats["low_conf"]}, avg={stats["avg_conf"]}')
    return stats


def main():
    parser = argparse.ArgumentParser(description='LLM-assisted ASR-Transcript alignment')
    parser.add_argument('--data-dir', default=DEFAULT_DATA_DIR)
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--llm-provider', default=None, help='openai|azure|generic')
    parser.add_argument('--llm-model', default=None)
    parser.add_argument('--llm-api-key', default=None)
    parser.add_argument('--llm-api-base', default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--file', default=None, help='Process only matching file stem')
    args = parser.parse_args()
    
    llm_config = {
        'provider': args.llm_provider or LLM_CONFIG.get('provider'),
        'api_key': args.llm_api_key or LLM_CONFIG.get('api_key') or os.environ.get('LLM_API_KEY'),
        'api_base': args.llm_api_base or LLM_CONFIG.get('api_base') or os.environ.get('LLM_API_BASE'),
        'model': args.llm_model or LLM_CONFIG.get('model') or os.environ.get('LLM_MODEL'),
        'temperature': 0.1,
        'max_tokens': 1024,
    }
    
    if llm_config['provider']:
        print(f'LLM: {llm_config["provider"]} / {llm_config["model"]}')
    else:
        print('LLM: not configured (algorithm-only)')
    
    xlsx_files = sorted(glob.glob(os.path.join(args.data_dir, '*__sentences.xlsx')))
    if args.file:
        xlsx_files = [f for f in xlsx_files if args.file in os.path.basename(f)]
    
    print(f'Found {len(xlsx_files)} files in {args.data_dir}')
    
    all_stats = []
    for xf in xlsx_files:
        stem = os.path.basename(xf).replace('__sentences.xlsx', '')
        tf = os.path.join(args.data_dir, stem + '__transcript_origin.txt')
        if not os.path.exists(tf):
            print(f'  SKIP {stem}: no txt')
            continue
        out = os.path.join(args.output_dir, stem + '__aligned.xlsx')
        stats = process_one_pair(xf, tf, out, llm_config, args.dry_run)
        all_stats.append(stats)
    
    # Summary
    print(f'\n{"="*80}')
    print(f'SUMMARY: {len(all_stats)} files')
    if all_stats:
        total = sum(s['xlsx_rows'] for s in all_stats)
        high = sum(s['high_conf'] for s in all_stats)
        low = sum(s['low_conf'] for s in all_stats)
        trunc = sum(1 for s in all_stats if s['truncated'])
        avg = sum(s['avg_conf'] for s in all_stats) / len(all_stats)
        print(f'  Rows: {total}, High: {high} ({high/max(total,1):.0%}), Low: {low} ({low/max(total,1):.0%})')
        print(f'  Truncated: {trunc}/{len(all_stats)}, Avg confidence: {avg:.2f}')
    
    if not args.dry_run and all_stats:
        summary_path = os.path.join(args.output_dir, '_summary.xlsx')
        os.makedirs(args.output_dir, exist_ok=True)
        pd.DataFrame(all_stats).to_excel(summary_path, index=False, engine='openpyxl')
        print(f'Summary: {summary_path}')


if __name__ == '__main__':
    main()
