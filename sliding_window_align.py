"""
Sliding-window semantic alignment for simultaneous interpretation evaluation.

Strategy:
1. Use origin text (human-verified) as the anchor.
2. Group origin lines into semantic paragraphs (target: 20-60 words each).
3. For each paragraph, find corresponding segments in M1 and M2 via
   character-offset mapping on the concatenated ASR streams.
4. Output: aligned table with [paragraph_id, origin, m1_asr, m1_trans, m2_asr, m2_trans].
"""
import pandas as pd
import re
from difflib import SequenceMatcher

# ── Load data ──
with open(r'D:\同传\4-已核对待评测-ASR+翻译+TTS数据\英文-已核对\en_36_9e9a1d04-28ce-47c2-9443-8d872a5c2e9d__e2e__en_zh__transcript_origin.txt', 'r', encoding='utf-8') as f:
    origin_lines = [l.strip() for l in f.readlines() if l.strip()]

df1 = pd.read_excel(r'D:\同传\4-已核对待评测-ASR+翻译+TTS数据\英文-已核对\en_36_9e9a1d04-28ce-47c2-9443-8d872a5c2e9d__e2e__en_zh__sentences.xlsx', header=None)
df2 = pd.read_excel(r'D:\同传\3-自研接口抓取-ASR+翻译+TTS\exported_logs-from自研接口原始数据\en2zh-94\en_36_f167c588-35b8-47c1-8210-56b77f8f9228__pipeline__en_zh\f167c588-35b8-47c1-8210-56b77f8f9228__pipeline__en_zh\en_36_f167c588-35b8-47c1-8210-56b77f8f9228__pipeline__en_zh__sentences.xlsx', header=None)

m1_asr_list = [str(x).strip() for x in df1.iloc[1:, 1].tolist()]
m1_trans_list = [str(x).strip() for x in df1.iloc[1:, 2].tolist()]
m2_asr_list = [str(x).strip() for x in df2.iloc[1:, 1].tolist()]
m2_trans_list = [str(x).strip() for x in df2.iloc[1:, 2].tolist()]

# ── Step 1: Build semantic paragraphs from origin ──
# Group by sentence-ending punctuation, then merge small groups to target 20-60 words
def build_paragraphs(lines, min_words=15, max_words=80):
    # First pass: group by sentence-ending punctuation
    raw_groups = []
    current = []
    for line in lines:
        current.append(line)
        if line and line[-1] in '.?!':
            raw_groups.append(current)
            current = []
    if current:
        raw_groups.append(current)
    
    # Second pass: merge small groups until we reach min_words
    paragraphs = []
    buf = []
    buf_words = 0
    for group in raw_groups:
        group_text = ' '.join(group)
        group_words = len(group_text.split())
        buf.extend(group)
        buf_words += group_words
        if buf_words >= min_words:
            paragraphs.append(buf)
            buf = []
            buf_words = 0
    if buf:
        if paragraphs:
            paragraphs[-1].extend(buf)
        else:
            paragraphs.append(buf)
    return paragraphs

paragraphs = build_paragraphs(origin_lines, min_words=20, max_words=80)
print(f'Semantic paragraphs: {len(paragraphs)}')
for i, p in enumerate(paragraphs[:5]):
    print(f'  [{i}] {len(p)} lines, {len(" ".join(p).split())} words: {" ".join(p)[:120]}...')

# ── Step 2: Build char-offset index for M1 and M2 ──
# For model X: concatenate all ASR segments with ' ' separator,
# record each segment's (char_start, char_end) in the concatenated string.
def build_segment_index(asr_list, trans_list):
    """Returns (full_text, segments) where segments[i] = (char_start, char_end, asr, trans)"""
    segments = []
    pos = 0
    parts = []
    for i, (asr, trans) in enumerate(zip(asr_list, trans_list)):
        start = pos
        parts.append(asr)
        pos += len(asr)
        if i < len(asr_list) - 1:
            parts.append(' ')
            pos += 1
        segments.append((start, pos, asr, trans))
    full_text = ''.join(parts)
    return full_text, segments

m1_full, m1_segs = build_segment_index(m1_asr_list, m1_trans_list)
m2_full, m2_segs = build_segment_index(m2_asr_list, m2_trans_list)

# Also build origin full text with char offsets per paragraph
origin_full = ' '.join(origin_lines)

# Map each origin line to its char range in origin_full
origin_line_offsets = []
pos = 0
for i, line in enumerate(origin_lines):
    start = pos
    pos += len(line)
    origin_line_offsets.append((start, pos))
    if i < len(origin_lines) - 1:
        pos += 1  # space separator

# ── Step 3: For M1 (ASR == origin), mapping is trivial ──
# M1 segments correspond 1:1 with origin lines

# ── Step 4: For M2, use word-level alignment to map origin char ranges → M2 char ranges ──
def normalize(text):
    """Lowercase, collapse whitespace, keep only alphanumeric + space"""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', text.lower())).strip()

def align_by_words(src_text, tgt_text):
    """
    Given src and tgt full texts, return a mapping: for each char position in src,
    estimate the corresponding char position in tgt.
    Uses word-level SequenceMatcher alignment.
    """
    src_words = src_text.split()
    tgt_words = tgt_text.split()
    
    # Build word → char offset maps
    def word_char_offsets(text, words):
        offsets = []
        pos = 0
        for w in words:
            idx = text.find(w, pos)
            if idx == -1:
                idx = pos
            offsets.append((idx, idx + len(w)))
            pos = idx + len(w)
        return offsets
    
    src_offsets = word_char_offsets(src_text, src_words)
    tgt_offsets = word_char_offsets(tgt_text, tgt_words)
    
    # Align words
    norm_src = [normalize(w) for w in src_words]
    norm_tgt = [normalize(w) for w in tgt_words]
    
    sm = SequenceMatcher(None, norm_src, norm_tgt)
    
    # Build src_word_idx → tgt_word_idx mapping
    word_map = {}  # src_word_idx → tgt_word_idx
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                word_map[i1 + k] = j1 + k
        elif tag == 'replace':
            # proportional mapping
            src_len = i2 - i1
            tgt_len = j2 - j1
            for k in range(src_len):
                word_map[i1 + k] = j1 + int(k * tgt_len / src_len)
        elif tag == 'delete':
            # map deleted src words to nearest tgt position
            for k in range(i2 - i1):
                word_map[i1 + k] = j1  # point to start of next tgt block
        # 'insert' doesn't need src mapping
    
    return src_offsets, tgt_offsets, word_map, src_words, tgt_words

print('\nAligning origin → M2 at word level...')
src_offsets, tgt_offsets, word_map, src_words, tgt_words = align_by_words(origin_full, m2_full)
print(f'Mapped {len(word_map)}/{len(src_words)} origin words → M2 positions')

# ── Step 5: For each paragraph, find M2 segment range ──
def find_segments_in_range(char_start, char_end, segments):
    """Find all segments that overlap with [char_start, char_end)"""
    result_asr = []
    result_trans = []
    for seg_start, seg_end, asr, trans in segments:
        # Check overlap
        if seg_end > char_start and seg_start < char_end:
            result_asr.append(asr)
            result_trans.append(trans)
    return ' '.join(result_asr), ' '.join(result_trans)

def para_to_char_range_origin(para_lines, origin_lines, origin_line_offsets):
    """Get char range of a paragraph (list of origin lines) in origin_full"""
    first_idx = None
    last_idx = None
    line_ptr = 0
    for i, line in enumerate(origin_lines):
        if line_ptr < len(para_lines) and line == para_lines[line_ptr]:
            if first_idx is None:
                first_idx = i
            last_idx = i
            line_ptr += 1
            if line_ptr == len(para_lines):
                break
    if first_idx is None:
        return 0, 0
    return origin_line_offsets[first_idx][0], origin_line_offsets[last_idx][1]

# For M2 mapping: convert origin char range → M2 char range via word alignment
def origin_range_to_m2_range(char_start, char_end):
    """Map an origin char range to the corresponding M2 char range via word alignment."""
    # Find origin words in this range
    start_word = None
    end_word = None
    for i, (ws, we) in enumerate(src_offsets):
        if we > char_start and start_word is None:
            start_word = i
        if ws < char_end:
            end_word = i
    
    if start_word is None or end_word is None:
        return 0, 0
    
    # Map to M2 word range
    m2_start_word = word_map.get(start_word, 0)
    m2_end_word = word_map.get(end_word, len(tgt_offsets) - 1)
    
    if m2_start_word >= len(tgt_offsets) or m2_end_word >= len(tgt_offsets):
        return 0, len(m2_full)
    
    m2_char_start = tgt_offsets[m2_start_word][0]
    m2_char_end = tgt_offsets[m2_end_word][1]
    return m2_char_start, m2_char_end

# ── Step 6: Build alignment table ──
# Track origin line index for M1 mapping
results = []
origin_ptr = 0

for para_idx, para_lines in enumerate(paragraphs):
    origin_text = ' '.join(para_lines)
    
    # M1: direct line mapping (M1 ASR == origin lines)
    m1_asr_parts = []
    m1_trans_parts = []
    for line in para_lines:
        idx = origin_lines.index(line) if origin_ptr == 0 else None
        # Use sequential pointer
        for j in range(origin_ptr, len(origin_lines)):
            if origin_lines[j] == line:
                idx = j
                break
        if idx is not None and idx < len(m1_asr_list):
            m1_asr_parts.append(m1_asr_list[idx])
            m1_trans_parts.append(m1_trans_list[idx])
    
    m1_asr_text = ' '.join(m1_asr_parts)
    m1_trans_text = ' | '.join(m1_trans_parts)
    
    # M2: via word alignment
    char_start, char_end = para_to_char_range_origin(para_lines, origin_lines, origin_line_offsets)
    m2_char_start, m2_char_end = origin_range_to_m2_range(char_start, char_end)
    m2_asr_text, m2_trans_text = find_segments_in_range(m2_char_start, m2_char_end, m2_segs)
    
    origin_ptr += len(para_lines)
    
    results.append({
        'para_id': para_idx + 1,
        'origin_lines': f'{origin_ptr - len(para_lines) + 1}-{origin_ptr}',
        'origin_text': origin_text,
        'm1_asr': m1_asr_text,
        'm1_translation': m1_trans_text,
        'm2_asr': m2_asr_text,
        'm2_translation': m2_trans_text,
    })

# ── Output ──
out_df = pd.DataFrame(results)
out_path = r'D:\POPO\AItester0227\AItester0227\aligned_output.xlsx'
out_df.to_excel(out_path, index=False)
print(f'\nWritten {len(results)} aligned paragraphs to {out_path}')

# Show first 8 paragraphs for review
print('\n' + '='*120)
for r in results[:8]:
    print(f"\n── Para {r['para_id']} (origin lines {r['origin_lines']}) ──")
    print(f"ORIGIN:   {r['origin_text'][:150]}")
    print(f"M1_ASR:   {r['m1_asr'][:150]}")
    print(f"M1_TRANS: {r['m1_translation'][:150]}")
    print(f"M2_ASR:   {r['m2_asr'][:150]}")
    print(f"M2_TRANS: {r['m2_translation'][:150]}")

# Quality check: how many paragraphs have empty M2 mapping?
empty_m2 = sum(1 for r in results if not r['m2_asr'].strip())
print(f'\n\nQuality: {empty_m2}/{len(results)} paragraphs have empty M2 mapping')

# Check M2 coverage: how many M2 segments are referenced at least once?
m2_referenced = set()
for r in results:
    for seg_start, seg_end, asr, trans in m2_segs:
        if asr in r['m2_asr']:
            m2_referenced.add(asr)
print(f'M2 segments referenced: {len(m2_referenced)}/{len(m2_segs)}')
