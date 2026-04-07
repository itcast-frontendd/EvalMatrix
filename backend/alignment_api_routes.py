# -*- coding: utf-8 -*-
"""
多模型数据对齐 API (基于 sliding_window_v2 算法)

核心思路：
1. 以原文 txt 为基准，按句末标点切分为语义段落
2. 模型1 (e2e) ASR 行与原文 1:1 → 直接按段落合并
3. 模型2 (pipeline) ASR 行与原文不对应 → 词级滑窗对齐
4. 文件分组 key: row_N 或 langcode_N（如 en_36, ja_12）
"""

import os, re, json, shutil, zipfile, traceback
from uuid import uuid4
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher

import pandas as pd
from fastapi import UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

_BACKEND = Path(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = _BACKEND / "uploads" / "alignment"
OUTPUT_DIR = _BACKEND / "outputs" / "alignment"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

alignment_jobs: Dict[str, dict] = {}

_norm_word = lambda w: re.sub(r'[^a-z0-9]', '', w.lower())  # v2: header-based column lookup, min_words=15

# ═══════════════════════════════════════════════════════════
# 核心对齐引擎 (移植自 sliding_window_v2.py)
# ═══════════════════════════════════════════════════════════

def _read_origin_txt(path: str) -> List[str]:
    with open(path, 'r', encoding='utf-8') as f:
        return [l.strip() for l in f if l.strip()]

def _read_model_excel(path: str) -> Tuple[List[str], List[str], Optional[List[str]]]:
    """
    按表头名定位列，兼容不同列数的 xlsx 格式
    返回: (asr_list, trans_list, ref_list_or_None)
      asr_list:  原文(STT) — 模型识别的原始文本
      trans_list: 译文 — 模型翻译
      ref_list:  更新ASR — 人工校正参考文本（可能不存在）
    """
    df = pd.read_excel(path, header=None)
    headers = [str(x).strip() for x in df.iloc[0].tolist()]

    # 查找 ASR 列：表头含 "原文" 或 "STT"
    asr_col = None
    for i, h in enumerate(headers):
        if '原文' in h or 'STT' in h:
            asr_col = i
            break

    # 查找译文列：表头为 "译文"
    trans_col = None
    for i, h in enumerate(headers):
        if h == '译文':
            trans_col = i
            break

    if asr_col is None:
        raise ValueError(f"未找到ASR列(原文/STT)，表头: {headers}，文件: {Path(path).name}")
    if trans_col is None:
        raise ValueError(f"未找到译文列，表头: {headers}，文件: {Path(path).name}")

    # 查找参考文本列：表头含 "更新ASR"
    ref_col = None
    for i, h in enumerate(headers):
        if '更新' in h and ('ASR' in h or 'asr' in h):
            ref_col = i
            break

    asr = [str(x).strip() if str(x).strip().lower() != 'nan' else '' for x in df.iloc[1:, asr_col].tolist()]
    trans = [str(x).strip() if str(x).strip().lower() != 'nan' else '' for x in df.iloc[1:, trans_col].tolist()]
    ref = [str(x).strip() if str(x).strip().lower() != 'nan' else '' for x in df.iloc[1:, ref_col].tolist()] if ref_col is not None else None
    return asr, trans, ref

def _is_cjk_dominant(text: str) -> bool:
    """检测文本是否以 CJK 字符为主（中日韩）"""
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af')
    return cjk > len(text) * 0.3

def _text_length(text: str, cjk_mode: bool) -> int:
    """CJK 文本用字符数，英文用词数"""
    return len(text) if cjk_mode else len(text.split())

def _build_paragraphs(lines, min_words=15):
    if not lines:
        return []

    # 检测是否 CJK 文本
    sample = ' '.join(lines[:5])
    cjk = _is_cjk_dominant(sample)
    # CJK 用字符数门槛（约等于 15 英文词 ≈ 30 个中日韩字符）
    min_len = min_words * 2 if cjk else min_words
    # CJK/英文句末标点
    end_puncts = '.?!。？！'

    raw_groups, cur = [], []
    for line in lines:
        cur.append(line)
        if line and line[-1] in end_puncts:
            raw_groups.append(cur); cur = []
    if cur: raw_groups.append(cur)

    paragraphs, buf, blen = [], [], 0
    for g in raw_groups:
        text = ' '.join(g)
        gl = _text_length(text, cjk)
        buf.extend(g); blen += gl
        if blen >= min_len:
            paragraphs.append(buf); buf = []; blen = 0
    if buf:
        (paragraphs[-1] if paragraphs else paragraphs).extend(buf) if paragraphs else paragraphs.append(buf)
    return paragraphs

def _build_seg_index(asr_list, trans_list):
    segs, pos = [], 0
    for i, (a, t) in enumerate(zip(asr_list, trans_list)):
        s = pos; pos += len(a)
        segs.append((s, pos, a, t))
        if i < len(asr_list) - 1: pos += 1
    return ' '.join(asr_list), segs

def _word_positions(text):
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r'\S+', text)]

def _build_word_alignment(origin_full, other_full):
    owp = _word_positions(origin_full)
    mwp = _word_positions(other_full)
    sm = SequenceMatcher(None, [_norm_word(w) for w,_,_ in owp], [_norm_word(w) for w,_,_ in mwp])
    o2m = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2-i1): o2m[i1+k] = j1+k
        elif tag == 'replace':
            sl, tl = i2-i1, j2-j1
            for k in range(sl): o2m[i1+k] = j1 + int(k*tl/sl)
        elif tag == 'delete':
            for k in range(i2-i1): o2m[i1+k] = j1
    return owp, mwp, o2m

def align_two_models(origin_lines, m1_asr, m1_trans, m2_asr, m2_trans, min_words=15):
    """核心对齐：M1 1:1, M2 词级滑窗"""
    paragraphs = _build_paragraphs(origin_lines, min_words)
    origin_full = ' '.join(origin_lines)
    # 行级字符范围
    olr, pos = [], 0
    for i, line in enumerate(origin_lines):
        olr.append((pos, pos+len(line)))
        pos += len(line) + (1 if i < len(origin_lines)-1 else 0)
    # M2 索引
    m2_full, m2_segs = _build_seg_index(m2_asr, m2_trans)
    owp, mwp, o2m = _build_word_alignment(origin_full, m2_full)

    def _wir(cs, ce, wps):
        return [i for i,(w,ws,we) in enumerate(wps) if ws>=cs and we<=ce]
    def _sir(cs, ce, segs):
        return [i for i,(ss,se,a,t) in enumerate(segs) if cs<=(ss+se)/2<=ce]

    results, optr, m2used = [], 0, set()
    for pidx, plines in enumerate(paragraphs):
        n = len(plines)
        os_ = olr[optr][0]
        oe_ = olr[min(optr+n-1, len(olr)-1)][1]
        # M1 直接合并
        m1i = list(range(optr, min(optr+n, len(m1_asr))))
        m1a = ' '.join(m1_asr[i] for i in m1i)
        m1t = ' ‖ '.join(m1_trans[i] for i in m1i)
        # M2 词级对齐
        owi = _wir(os_, oe_, owp)
        m2si = []
        if owi:
            ms = o2m.get(owi[0], 0)
            me = o2m.get(owi[-1], len(mwp)-1)
            mcs = mwp[ms][1]
            mce = mwp[min(me, len(mwp)-1)][2]
            m2si = _sir(mcs, mce, m2_segs)
        m2si = [i for i in m2si if i not in m2used]
        m2used.update(m2si)
        m2a = ' '.join(m2_segs[i][2] for i in m2si)
        m2t = ' ‖ '.join(m2_segs[i][3] for i in m2si)
        otext = ' '.join(plines)
        onorm = set(_norm_word(w) for w in otext.split() if _norm_word(w))
        mnorm = set(_norm_word(w) for w in m2a.split() if _norm_word(w))
        overlap = len(onorm & mnorm) / max(len(onorm), 1)
        results.append({
            'sentence_id': pidx+1,
            'origin_lines': f'{optr+1}-{optr+n}',
            'asr_text': otext,
            'translation_model1': m1t,
            'translation_model2': m2t,
            'm1_asr': m1a, 'm2_asr': m2a,
            'word_overlap': round(overlap, 3),
        })
        optr += n
    return results

# ═══════════════════════════════════════════════════════════
# 文件扫描与分组
# ═══════════════════════════════════════════════════════════

def _extract_group_key(filename: str) -> Optional[str]:
    """row_4_xxx → row_4, en_36_xxx → en_36, ja_12_xxx → ja_12"""
    m = re.match(r'^(\w+_\d+)_', filename)
    return m.group(1) if m else None

def _scan_folder(folder: Path, model_tags: List[str]):
    tag_pat = "|".join(re.escape(t) for t in model_tags)
    # lang pair 可能含连字符如 ja_zh-CHS，用 .+? 而非 \w+_\w+
    xlsx_re = re.compile(rf'^(.+?)__({tag_pat})__(.+?)__sentences\.xlsx$', re.I)
    groups: Dict[str, Dict] = {}
    # xlsx
    for f in folder.rglob("*__sentences.xlsx"):
        if f.name.startswith("~$"): continue
        m = xlsx_re.match(f.name)
        if not m: continue
        fp, mt = m.group(1), m.group(2)
        gk = _extract_group_key(f.name)
        if not gk: continue
        if gk not in groups: groups[gk] = {"group_key": gk, "txt_path": None, "models": {}}
        groups[gk]["models"][mt] = {"xlsx": f, "full_prefix": fp}
    # txt
    for f in folder.rglob("*__transcript_origin.txt"):
        if f.name.startswith("~$"): continue
        gk = _extract_group_key(f.name)
        if gk and gk in groups and not groups[gk]["txt_path"]:
            groups[gk]["txt_path"] = f
        elif gk and gk not in groups:
            groups[gk] = {"group_key": gk, "txt_path": f, "models": {}}
    return sorted([g for g in groups.values() if g["models"]], key=lambda g: g["group_key"])

# ═══════════════════════════════════════════════════════════
# 后台任务
# ═══════════════════════════════════════════════════════════

def _add_ctx(rows, w=2):
    for i, r in enumerate(rows):
        r["context_before"] = " | ".join(rows[j]["asr_text"] for j in range(max(0,i-w),i) if rows[j]["asr_text"])
        r["context_after"]  = " | ".join(rows[j]["asr_text"] for j in range(i+1,min(len(rows),i+w+1)) if rows[j]["asr_text"])
    return rows

def _clean_nan_values(rows):
    """将所有值中的 'nan' / NaN 转为空字符串"""
    for r in rows:
        for k, v in r.items():
            if v is None:
                r[k] = ""
            elif isinstance(v, float) and (v != v):  # NaN check
                r[k] = ""
            elif isinstance(v, str) and v.strip().lower() == 'nan':
                r[k] = ""
    return rows

def _normalize_for_compare(text: str) -> str:
    """去除标点和空格，用于语义比对"""
    return re.sub(r"[\s\u3000,，。.!！?？;；:：、\-—~～…·'\"''\u201c\u201d()（）【】{}「」『』<>《》\u200b\[\]]", '', text).lower()

def _char_overlap(txt_norm: str, asr_norm: str) -> float:
    """计算 txt 归一化文本在 asr 归一化文本中的覆盖率（召回率）。
    
    核心指标：txt 中有多少字符能在 asr 中找到匹配（保持顺序）。
    txt 是标准文本，asr 是模型识别文本，可能有额外内容。
    """
    if not txt_norm or not asr_norm:
        return 0.0
    # 如果 txt 是 asr 的子串 → 完美匹配
    if txt_norm in asr_norm:
        return 1.0
    # 用 SequenceMatcher 找最长公共子序列
    sm = SequenceMatcher(None, txt_norm, asr_norm)
    # 计算 txt 中被匹配的字符比例（召回率）
    matched_chars = sum(block.size for block in sm.get_matching_blocks())
    recall = matched_chars / len(txt_norm)
    return recall

def _semantic_align_txt_to_xlsx(txt_lines: List[str], asr_list: List[str], trans_list: List[str]) -> List[Dict]:
    """
    以 txt 行为基准，将 xlsx 的 asr/trans 语义对齐。
    
    自动选择策略：
    - 行数差异 <= 30%: 渐进式贪心（严格单调，紧凑匹配）
    - 行数差异 > 30%:  全局 SequenceMatcher + 单调性修正
    """
    n_txt = len(txt_lines)
    n_xlsx = len(asr_list)
    
    if n_xlsx == 0:
        return [{'txt_idx': i, 'txt_line': txt_lines[i], 'xlsx_indices': [],
                 'asr_combined': '', 'trans_combined': '', 'overlap': 0.0} for i in range(n_txt)]
    
    txt_norms = [_normalize_for_compare(l) for l in txt_lines]
    asr_norms = [_normalize_for_compare(a) for a in asr_list]
    
    diff_ratio = abs(n_txt - n_xlsx) / max(n_txt, n_xlsx, 1)
    
    if diff_ratio <= 0.3:
        aligned = _align_greedy(txt_lines, asr_list, trans_list, txt_norms, asr_norms)
    else:
        aligned = _align_global(txt_lines, asr_list, trans_list, txt_norms, asr_norms)
    
    return aligned


def _align_greedy(txt_lines, asr_list, trans_list, txt_norms, asr_norms):
    """渐进式贪心对齐 — 适用于行数接近的数据"""
    n_txt = len(txt_lines)
    n_xlsx = len(asr_list)
    
    def _score(txt_norm, asr_combined, n_rows):
        if not txt_norm or not asr_combined:
            return 0.0
        recall = _char_overlap(txt_norm, asr_combined)
        precision = _char_overlap(asr_combined, txt_norm)
        if recall + precision == 0:
            return 0.0
        f1 = 2 * recall * precision / (recall + precision)
        return f1 / (1.0 + 0.05 * max(0, n_rows - 1))
    
    aligned = []
    ptr = 0
    
    for ti in range(n_txt):
        txt_norm = txt_norms[ti]
        if not txt_norm:
            aligned.append({'txt_idx': ti, 'txt_line': txt_lines[ti], 'xlsx_indices': [],
                           'asr_combined': '', 'trans_combined': '', 'overlap': 0.0})
            continue
        
        remaining_txt = max(n_txt - ti, 1)
        remaining_xlsx = max(n_xlsx - ptr, 1)
        ratio = remaining_xlsx / remaining_txt
        window = max(4, min(8, int(ratio * 2)))
        
        search_lo = max(0, ptr - 1)
        search_hi = min(n_xlsx, ptr + window)
        
        best_indices, best_score, best_overlap = [], 0.0, 0.0
        best_asr, best_trans = '', ''
        
        for start_j in range(search_lo, search_hi):
            acc_norm = ''
            indices = []
            for j in range(start_j, min(n_xlsx, start_j + 3)):
                acc_norm += asr_norms[j]
                indices.append(j)
                score = _score(txt_norm, acc_norm, len(indices))
                overlap = _char_overlap(txt_norm, acc_norm)
                if score > best_score:
                    best_score = score
                    best_overlap = overlap
                    best_indices = list(indices)
                    best_asr = ' '.join(asr_list[idx] for idx in indices)
                    best_trans = ' '.join(trans_list[idx] for idx in indices)
                if score > 0.9 or len(acc_norm) > len(txt_norm) * 2:
                    break
            if best_score > 0.9:
                break
        
        if best_indices and best_score >= 0.3:
            new_end = best_indices[-1] + 1
            if new_end > ptr:
                ptr = new_end
        elif not best_indices or best_score < 0.3:
            expected = int((ti + 1) * n_xlsx / n_txt)
            if expected > ptr:
                ptr = min(expected, n_xlsx)
        
        has_match = best_score >= 0.15
        aligned.append({
            'txt_idx': ti, 'txt_line': txt_lines[ti],
            'xlsx_indices': best_indices if has_match else [],
            'asr_combined': best_asr if has_match else '',
            'trans_combined': best_trans if has_match else '',
            'overlap': round(best_overlap if has_match else 0.0, 3),
        })
    
    # 局部修正低质量段
    _repair_low_segments(aligned, asr_list, trans_list, txt_norms, asr_norms)
    # 单调性
    _enforce_monotonic(aligned, asr_list, trans_list, txt_norms)
    
    return aligned


def _align_global(txt_lines, asr_list, trans_list, txt_norms, asr_norms):
    """全局 SequenceMatcher 对齐 — 适用于行数差异大的数据"""
    n_txt = len(txt_lines)
    n_xlsx = len(asr_list)
    
    asr_concat = ''.join(asr_norms)
    asr_ranges = []
    pos = 0
    for norm in asr_norms:
        asr_ranges.append((pos, pos + len(norm)))
        pos += len(norm)
    
    txt_concat = ''.join(txt_norms)
    txt_ranges = []
    pos = 0
    for norm in txt_norms:
        txt_ranges.append((pos, pos + len(norm)))
        pos += len(norm)
    
    sm = SequenceMatcher(None, txt_concat, asr_concat)
    opcodes = sm.get_opcodes()
    
    txt2asr = {}
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'equal':
            for k in range(i2 - i1):
                txt2asr[i1 + k] = j1 + k
        elif tag == 'replace':
            tlen, alen = i2 - i1, j2 - j1
            for k in range(tlen):
                txt2asr[i1 + k] = j1 + int(k * alen / tlen)
        elif tag == 'delete':
            for k in range(i2 - i1):
                txt2asr[i1 + k] = j1
    
    aligned = []
    for ti in range(n_txt):
        txt_start, txt_end = txt_ranges[ti]
        if txt_start >= txt_end:
            aligned.append({'txt_idx': ti, 'txt_line': txt_lines[ti], 'xlsx_indices': [],
                           'asr_combined': '', 'trans_combined': '', 'overlap': 0.0})
            continue
        
        asr_positions = set()
        for cp in range(txt_start, txt_end):
            if cp in txt2asr:
                asr_positions.add(txt2asr[cp])
        
        if not asr_positions:
            aligned.append({'txt_idx': ti, 'txt_line': txt_lines[ti], 'xlsx_indices': [],
                           'asr_combined': '', 'trans_combined': '', 'overlap': 0.0})
            continue
        
        asr_min = min(asr_positions)
        asr_max = max(asr_positions)
        
        xlsx_indices = [xi for xi in range(n_xlsx)
                       if asr_ranges[xi][0] <= asr_max and asr_ranges[xi][1] > asr_min]
        
        asr_combined = ' '.join(asr_list[j] for j in xlsx_indices)
        trans_combined = ' '.join(trans_list[j] for j in xlsx_indices)
        overlap = _char_overlap(txt_norms[ti], _normalize_for_compare(asr_combined))
        
        aligned.append({
            'txt_idx': ti, 'txt_line': txt_lines[ti],
            'xlsx_indices': xlsx_indices,
            'asr_combined': asr_combined,
            'trans_combined': trans_combined,
            'overlap': round(overlap, 3),
        })
    
    # 局部修正
    _repair_low_segments(aligned, asr_list, trans_list, txt_norms, asr_norms)
    # 单调性
    _enforce_monotonic(aligned, asr_list, trans_list, txt_norms)
    
    return aligned


def _repair_low_segments(aligned, asr_list, trans_list, txt_norms, asr_norms):
    """修正连续低质量段（overlap < 0.4 且 >= 3 行）"""
    n_xlsx = len(asr_list)
    
    low_segs = []
    seg_start = None
    for i, a in enumerate(aligned):
        if a['overlap'] < 0.4 and txt_norms[a['txt_idx']]:
            if seg_start is None:
                seg_start = i
        else:
            if seg_start is not None and i - seg_start >= 3:
                low_segs.append((seg_start, i))
            seg_start = None
    if seg_start is not None and len(aligned) - seg_start >= 3:
        low_segs.append((seg_start, len(aligned)))
    
    for seg_s, seg_e in low_segs:
        pre_xlsx = 0
        for k in range(seg_s - 1, -1, -1):
            if aligned[k]['xlsx_indices'] and aligned[k]['overlap'] >= 0.5:
                pre_xlsx = max(aligned[k]['xlsx_indices']) + 1
                break
        post_xlsx = n_xlsx
        for k in range(seg_e, min(len(aligned), seg_e + 10)):
            if aligned[k]['xlsx_indices'] and aligned[k]['overlap'] >= 0.5:
                post_xlsx = min(aligned[k]['xlsx_indices'])
                break
        
        seg_len = seg_e - seg_s
        xlsx_span = post_xlsx - pre_xlsx
        
        for idx in range(seg_s, seg_e):
            ti_val = aligned[idx]['txt_idx']
            if not txt_norms[ti_val]:
                continue
            frac = (idx - seg_s) / max(seg_len, 1)
            est = int(pre_xlsx + frac * xlsx_span)
            lo = max(0, est - 4)
            hi = min(n_xlsx, est + 5)
            
            b_idx, b_ov, b_asr, b_tr = [], 0.0, '', ''
            for sj in range(lo, hi):
                acc = ''
                ids = []
                for j in range(sj, min(hi + 2, n_xlsx, sj + 4)):
                    acc += asr_norms[j]
                    ids.append(j)
                    ov = _char_overlap(txt_norms[ti_val], acc)
                    if ov > b_ov:
                        b_ov = ov
                        b_idx = list(ids)
                        b_asr = ' '.join(asr_list[jj] for jj in ids)
                        b_tr = ' '.join(trans_list[jj] for jj in ids)
                    if ov > 0.95 or len(acc) > len(txt_norms[ti_val]) * 2:
                        break
            
            if b_ov > aligned[idx]['overlap']:
                aligned[idx] = {
                    'txt_idx': ti_val, 'txt_line': aligned[idx]['txt_line'],
                    'xlsx_indices': b_idx, 'asr_combined': b_asr,
                    'trans_combined': b_tr, 'overlap': round(b_ov, 3),
                }


def _enforce_monotonic(aligned, asr_list, trans_list, txt_norms):
    """强制单调性：丢弃非递增的索引"""
    prev_max = -1
    for a in aligned:
        if not a['xlsx_indices']:
            continue
        cur_min = min(a['xlsx_indices'])
        if cur_min < prev_max - 1:
            fixed = [j for j in a['xlsx_indices'] if j >= prev_max - 1]
            if not fixed:
                a['xlsx_indices'] = []
                a['asr_combined'] = ''
                a['trans_combined'] = ''
                a['overlap'] = 0.0
            else:
                a['xlsx_indices'] = fixed
                a['asr_combined'] = ' '.join(asr_list[j] for j in fixed)
                a['trans_combined'] = ' '.join(trans_list[j] for j in fixed)
                a['overlap'] = round(_char_overlap(
                    txt_norms[a['txt_idx']],
                    _normalize_for_compare(a['asr_combined'])
                ), 3)
        if a['xlsx_indices']:
            prev_max = max(prev_max, max(a['xlsx_indices']))
def _process_single_model(gk, group, tags, min_words, context_window):
    """处理单模型数据组：以 txt 为基准构建 reference_text
    
    核心改动：
    - reference_text (asr_text) 以文件夹中的 txt 为基准
    - 当 txt 与 xlsx 行数差异较大时，使用语义对齐算法
    - xlsx 的 ASR 结果可能标点有错漏，不做机械对齐，以语义为核心
    """
    tag = list(group["models"].keys())[0]
    xlsx_path = str(group["models"][tag]["xlsx"])
    asr_list, trans_list, ref_list = _read_model_excel(xlsx_path)
    
    n_xlsx = len(asr_list)
    
    # 读取 txt 原文（如果存在）
    txt_lines = []
    if group.get("txt_path"):
        txt_lines = _read_origin_txt(str(group["txt_path"]))
    
    if txt_lines:
        # ── 以 txt 为基准 ──
        n_txt = len(txt_lines)
        row_diff_ratio = abs(n_txt - n_xlsx) / max(n_txt, n_xlsx, 1)
        
        print(f"[ALIGN] {gk}: txt={n_txt} lines, xlsx={n_xlsx} rows, diff_ratio={row_diff_ratio:.2%}", flush=True)
        
        # 语义对齐: txt → xlsx (无论行数差异大小，都用语义对齐确保质量)
        aligned = _semantic_align_txt_to_xlsx(txt_lines, asr_list, trans_list)
        
        # 以 txt 行做段落合并
        paragraphs = _build_paragraphs(txt_lines, min_words=min_words)
        
        rows = []
        txt_ptr = 0
        for pidx, plines in enumerate(paragraphs):
            n = len(plines)
            end = min(txt_ptr + n, n_txt)
            txt_indices = list(range(txt_ptr, end))
            
            if not txt_indices:
                break
            
            # reference_text: 来自 txt 原文
            ref_text = ' '.join(txt_lines[i] for i in txt_indices)
            
            # 从语义对齐结果中收集对应的 xlsx 行
            all_xlsx_idx = []
            para_overlap_sum = 0.0
            for ti in txt_indices:
                if ti < len(aligned):
                    all_xlsx_idx.extend(aligned[ti]['xlsx_indices'])
                    para_overlap_sum += aligned[ti]['overlap']
            # 去重并保持顺序
            seen = set()
            unique_xlsx_idx = []
            for idx in all_xlsx_idx:
                if idx not in seen:
                    seen.add(idx)
                    unique_xlsx_idx.append(idx)
            
            model_asr = ' '.join(asr_list[j] for j in unique_xlsx_idx) if unique_xlsx_idx else ''
            model_trans = ' ‖ '.join(trans_list[j] for j in unique_xlsx_idx) if unique_xlsx_idx else ''
            avg_overlap = para_overlap_sum / max(len(txt_indices), 1)
            
            rows.append({
                'sentence_id': pidx + 1,
                'origin_lines': f'{txt_ptr + 1}-{end}',
                'asr_text': ref_text,
                f'translation_{tag}': model_trans,
                f'asr_{tag}': model_asr,
                'word_overlap': round(avg_overlap, 3),
            })
            txt_ptr = end
        
        # 尾部
        if txt_ptr < n_txt:
            txt_indices = list(range(txt_ptr, n_txt))
            ref_text = ' '.join(txt_lines[i] for i in txt_indices)
            all_xlsx_idx = []
            for ti in txt_indices:
                if ti < len(aligned):
                    all_xlsx_idx.extend(aligned[ti]['xlsx_indices'])
            seen = set()
            unique_xlsx_idx = [idx for idx in all_xlsx_idx if idx not in seen and not seen.add(idx)]
            model_asr = ' '.join(asr_list[j] for j in unique_xlsx_idx) if unique_xlsx_idx else ''
            model_trans = ' ‖ '.join(trans_list[j] for j in unique_xlsx_idx) if unique_xlsx_idx else ''
            rows.append({
                'sentence_id': len(rows) + 1,
                'origin_lines': f'{txt_ptr + 1}-{n_txt}',
                'asr_text': ref_text,
                f'translation_{tag}': model_trans,
                f'asr_{tag}': model_asr,
                'word_overlap': 0.5,
            })
        
        # 统计对齐质量
        total_overlap = sum(a['overlap'] for a in aligned if a['xlsx_indices'])
        matched_count = sum(1 for a in aligned if a['xlsx_indices'])
        avg_quality = total_overlap / max(matched_count, 1)
        print(f"[ALIGN] {gk}: aligned {matched_count}/{n_txt} txt lines, avg_overlap={avg_quality:.3f}", flush=True)
        
    else:
        # ── 无 txt：回退到原始 xlsx 逻辑 ──
        has_ref = ref_list is not None and len(ref_list) == n_xlsx and any(r and r != 'nan' for r in ref_list)
        lines_for_para = ref_list if has_ref else asr_list
        paragraphs = _build_paragraphs(lines_for_para, min_words=min_words)
        
        rows = []
        ptr = 0
        for pidx, plines in enumerate(paragraphs):
            n = len(plines)
            end = min(ptr + n, n_xlsx)
            indices = list(range(ptr, end))
            
            if not indices:
                break
            
            if has_ref:
                ref_text = ' '.join(ref_list[i] for i in indices)
            else:
                ref_text = ' '.join(asr_list[i] for i in indices)
            
            model_asr = ' '.join(asr_list[i] for i in indices)
            model_trans = ' ‖ '.join(trans_list[i] for i in indices)
            
            rows.append({
                'sentence_id': pidx + 1,
                'origin_lines': f'{ptr + 1}-{end}',
                'asr_text': ref_text,
                f'translation_{tag}': model_trans,
                f'asr_{tag}': model_asr,
                'word_overlap': 1.0,
            })
            ptr = end
        
        if ptr < n_xlsx:
            indices = list(range(ptr, n_xlsx))
            if has_ref:
                ref_text = ' '.join(ref_list[i] for i in indices)
            else:
                ref_text = ' '.join(asr_list[i] for i in indices)
            model_asr = ' '.join(asr_list[i] for i in indices)
            model_trans = ' ‖ '.join(trans_list[i] for i in indices)
            rows.append({
                'sentence_id': len(rows) + 1,
                'origin_lines': f'{ptr + 1}-{n_xlsx}',
                'asr_text': ref_text,
                f'translation_{tag}': model_trans,
                f'asr_{tag}': model_asr,
                'word_overlap': 1.0,
            })
    
    rows = _add_ctx(rows, context_window)
    model_keys = [f"translation_{tag}"]
    return rows, model_keys, [tag]


def _process_multi_model(gk, group, tags, min_words, context_window):
    """处理多模型数据组：滑窗对齐"""
    mkeys = sorted(group["models"].keys())
    t1, t2 = mkeys[0], mkeys[1]

    ol = _read_origin_txt(str(group["txt_path"]))
    a1, tr1, ref1 = _read_model_excel(str(group["models"][t1]["xlsx"]))
    a2, tr2, ref2 = _read_model_excel(str(group["models"][t2]["xlsx"]))

    # 如果有"更新ASR"列，用它作为 reference_text
    # 优先从 model1 取（因为 model1 的行与 txt 1:1）
    has_ref = ref1 is not None and any(r and r != 'nan' for r in ref1)

    rows = align_two_models(ol, a1, tr1, a2, tr2, min_words=min_words)

    # 重命名列 + 添加 ref_text
    for i, r in enumerate(rows):
        r[f"translation_{t1}"] = r.pop("translation_model1")
        r[f"translation_{t2}"] = r.pop("translation_model2")
        r[f"asr_{t1}"] = r.pop("m1_asr")
        r[f"asr_{t2}"] = r.pop("m2_asr")
        # 如果有更新ASR，覆盖 asr_text
        if has_ref:
            # 按 origin_lines 范围从 ref1 提取
            line_range = r["origin_lines"]
            parts = line_range.split("-")
            start, end = int(parts[0]) - 1, int(parts[1])
            ref_segment = ' '.join(ref1[start:min(end, len(ref1))])
            if ref_segment.strip():
                r["asr_text"] = ref_segment

    rows = _add_ctx(rows, context_window)
    model_keys = [f"translation_{t1}", f"translation_{t2}"]
    return rows, model_keys, [t1, t2]


def _run_alignment(job_id, extract_dir, config):
    job = alignment_jobs[job_id]
    try:
        tags = config.get("model_tags", ["e2e", "pipeline"])
        names = config.get("model_names") or tags
        cw = config.get("context_window", 2)
        mw = config.get("min_segment_words", 15)

        job.update(status="processing", progress=10, message="扫描文件...")
        groups = _scan_folder(Path(extract_dir), tags)
        job["group_count"] = len(groups)

        if not groups:
            job.update(status="failed", message=f"未找到匹配文件。期望: {{prefix}}__{{{'/'.join(tags)}}}__{{lang}}__sentences.xlsx")
            return

        job.update(progress=20, message=f"发现 {len(groups)} 组，开始处理...")
        out = OUTPUT_DIR / job_id / "output"
        out.mkdir(parents=True, exist_ok=True)

        finfo, total, errs = [], 0, []
        for gi, g in enumerate(groups):
            job.update(progress=20+int(60*gi/len(groups)), message=f"处理 {gi+1}/{len(groups)}: {g['group_key']}")
            gk = g["group_key"]

            if not g["models"]:
                errs.append(f"{gk}: 无模型文件"); continue

            try:
                n_models = len(g["models"])
                otxt = ""
                if g["txt_path"]:
                    try:
                        with open(g["txt_path"], "r", encoding="utf-8") as f:
                            otxt = f.read().strip()
                    except: pass

                if n_models == 1:
                    # 单模型处理
                    rows, mk, model_tags_used = _process_single_model(gk, g, tags, mw, cw)
                elif n_models >= 2:
                    if not g["txt_path"]:
                        errs.append(f"{gk}: 多模型但缺少原文txt"); continue
                    rows, mk, model_tags_used = _process_multi_model(gk, g, tags, mw, cw)
                else:
                    continue

                # 清理 nan 值
                rows = _clean_nan_values(rows)

                # 写 JSONL
                jn = f"{gk}_eval.jsonl"
                with open(out / jn, "w", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")

                il = len(otxt) > 10000
                finfo.append({
                    "file_id": gk, "jsonl_file": jn, "group_key": gk,
                    "models": model_tags_used, "model_keys": mk,
                    "row_count": len(rows),
                    "origin_input": "" if il else otxt,
                    "origin_input_chars": len(otxt),
                    "use_file_context": not il, "context_window": cw,
                    "avg_overlap": round(sum(r.get("word_overlap", 1.0) for r in rows) / max(len(rows), 1), 3),
                    "low_quality": sum(1 for r in rows if r.get("word_overlap", 1.0) < 0.5),
                })
                total += len(rows)

            except Exception as e:
                errs.append(f"{gk}: {e}")
                traceback.print_exc()

        # ── 生成 manifest 和 ZIP ──
        job.update(progress=85, message="生成输出文件...")
        amk = []
        for fi in finfo:
            for k in fi["model_keys"]:
                if k not in amk:
                    amk.append(k)
        dm = {}
        for k in amk:
            t = k.replace("translation_", "")
            i = tags.index(t) if t in tags else -1
            dm[k] = names[i] if 0 <= i < len(names) else t

        # model_asr_fields
        asr_fields = {}
        for k in amk:
            tag = k.replace("translation_", "")
            asr_fields[k] = f"asr_{tag}"

        is_multi = len(amk) > 1
        eval_type = "multi_model_translation_comparison" if is_multi else "single_model_translation"
        desc = "多模型滑窗对齐评测数据" if is_multi else "单模型评测数据"

        manifest = {
            "version": "3.0", "description": desc,
            "evaluation_type": eval_type,
            "created_at": datetime.now().isoformat(),
            "eval_config": {
                "mapping": {
                    "question": "asr_text",
                    "models": amk,
                    "model_asr_fields": asr_fields,
                    "context_fields": ["context_before", "context_after"],
                },
                "model_display_names": dm,
                "default_scenario": "", "default_dimensions": [],
                "recommended_batch_size": 5, "recommended_concurrency": 2,
            },
            "files": finfo, "total_rows": total,
            "model_count": len(amk), "model_keys": amk,
        }

        with open(out / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        with open(out / "all_eval_merged.jsonl", "w", encoding="utf-8") as fo:
            for fi in finfo:
                p = out / fi["jsonl_file"]
                if p.exists():
                    for ln in open(p, "r", encoding="utf-8"):
                        rec = json.loads(ln)
                        rec["file_id"] = fi["file_id"]
                        fo.write(json.dumps(rec, ensure_ascii=False) + "\n")

        zp = out / "eval_project.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(out / "manifest.json", "manifest.json")
            for fi in finfo:
                p = out / fi["jsonl_file"]
                if p.exists():
                    zf.write(p, fi["jsonl_file"])
            if (out / "all_eval_merged.jsonl").exists():
                zf.write(out / "all_eval_merged.jsonl", "all_eval_merged.jsonl")

        job.update(progress=100, status="completed", message=f"完成: {len(finfo)} 组 / {total} 行",
            file_count=len(finfo), errors=errs,
            result={"output_dir":str(out),"manifest":manifest,"zip_path":str(zp)})
    except Exception as e:
        job.update(status="failed", message=f"失败: {e}", progress=0)
        traceback.print_exc()

# ═══════════════════════════════════════════════════════════
# 路由注册
# ═══════════════════════════════════════════════════════════

def register_alignment_routes(app):

    @app.post("/api/alignment/upload")
    async def alignment_upload(background_tasks: BackgroundTasks, file: UploadFile=File(...), config: str=Form(default="{}")):
        jid = f"aln_{datetime.now().strftime('%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        try: cfg = json.loads(config)
        except Exception as e: raise HTTPException(400, f"config 解析失败: {e}")
        alignment_jobs[jid] = {"job_id":jid,"status":"uploading","progress":0,"message":"上传中...","config":cfg,
            "created_at":datetime.now().isoformat(),"file_count":0,"group_count":0,"result":None,"errors":[]}
        jd = UPLOAD_DIR/jid; jd.mkdir(parents=True,exist_ok=True)
        up = jd/file.filename
        with open(up,"wb") as f: f.write(await file.read())
        ed = jd/"extracted"; ed.mkdir(exist_ok=True)
        if file.filename.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(up,"r") as zf: zf.extractall(ed)
            except zipfile.BadZipFile: raise HTTPException(400,"无效的ZIP")
        else: raise HTTPException(400,"仅支持 .zip")
        alignment_jobs[jid]["upload_path"] = str(up)
        background_tasks.add_task(_run_alignment, jid, str(ed), cfg)
        return {"job_id":jid,"status":"processing","message":"上传成功，后台处理中"}

    @app.get("/api/alignment/status/{job_id}")
    async def alignment_status(job_id: str):
        if job_id not in alignment_jobs: raise HTTPException(404,"任务不存在")
        j = alignment_jobs[job_id]
        return {k:j.get(k) for k in ["job_id","status","progress","message","group_count","file_count","errors"]}

    @app.get("/api/alignment/preview/{job_id}")
    async def alignment_preview(job_id: str, file_index: int=0, max_rows: int=15):
        if job_id not in alignment_jobs: raise HTTPException(404,"任务不存在")
        j = alignment_jobs[job_id]
        if j["status"]!="completed": raise HTTPException(400,f"未完成({j['status']})")
        r = j["result"]; m = r["manifest"]; od = Path(r["output_dir"]); fs = m["files"]
        if file_index>=len(fs): raise HTTPException(400,f"越界(共{len(fs)})")
        fi = fs[file_index]; jp = od/fi["jsonl_file"]
        rows, total = [], 0
        with open(jp,"r",encoding="utf-8") as f:
            for ln in f:
                total+=1
                if len(rows)<max_rows: rows.append(json.loads(ln))
        return {"job_id":job_id,"file_info":fi,"total_files":len(fs),"file_index":file_index,
            "total_rows":total,"preview_count":len(rows),"columns":list(rows[0].keys()) if rows else [],
            "data":rows,"model_keys":m.get("model_keys",[]),"model_display_names":m["eval_config"].get("model_display_names",{})}

    @app.post("/api/alignment/confirm/{job_id}")
    async def alignment_confirm(job_id: str):
        if job_id not in alignment_jobs: raise HTTPException(404,"任务不存在")
        j = alignment_jobs[job_id]
        if j["status"]!="completed": raise HTTPException(400,"未完成")
        r = j["result"]
        if not Path(r["zip_path"]).exists(): raise HTTPException(500,"ZIP不存在")
        return {"status":"confirmed","job_id":job_id,"zip_path":r["zip_path"],"manifest":r["manifest"]}

    @app.get("/api/alignment/download/{job_id}")
    async def alignment_download(job_id: str, file_type: str="zip"):
        if job_id not in alignment_jobs: raise HTTPException(404,"任务不存在")
        j = alignment_jobs[job_id]
        if j["status"]!="completed": raise HTTPException(400,"未完成")
        nm = {"zip":"eval_project.zip","manifest":"manifest.json","merged":"all_eval_merged.jsonl"}
        fn = nm.get(file_type)
        if not fn: raise HTTPException(400,f"不支持: {file_type}")
        fp = Path(j["result"]["output_dir"])/fn
        if not fp.exists(): raise HTTPException(404,"文件不存在")
        return FileResponse(fp, filename=fn, media_type="application/octet-stream")

    @app.delete("/api/alignment/jobs/{job_id}")
    async def alignment_delete(job_id: str):
        if job_id not in alignment_jobs: raise HTTPException(404,"任务不存在")
        for d in [UPLOAD_DIR/job_id, OUTPUT_DIR/job_id]:
            if d.exists(): shutil.rmtree(d, ignore_errors=True)
        del alignment_jobs[job_id]
        return {"status":"deleted","job_id":job_id}

    @app.get("/api/alignment/jobs")
    async def alignment_list(limit: int=50):
        jobs = [{"job_id":j["job_id"],"status":j["status"],"progress":j.get("progress",0),
            "message":j.get("message",""),"created_at":j.get("created_at",""),
            "group_count":j.get("group_count",0),"file_count":j.get("file_count",0)}
            for j in list(alignment_jobs.values())[-limit:]]
        return {"jobs":jobs,"total":len(alignment_jobs)}

    print("[INFO] Alignment API routes registered (sliding_window_v2 engine)", flush=True)
