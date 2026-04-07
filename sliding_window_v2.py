"""
Sliding-window alignment v2 — with overlap dedup and quality diagnostics.
"""
import pandas as pd
import re
from difflib import SequenceMatcher

# ── Load ──
with open(r'D:\同传\4-已核对待评测-ASR+翻译+TTS数据\英文-已核对\en_36_9e9a1d04-28ce-47c2-9443-8d872a5c2e9d__e2e__en_zh__transcript_origin.txt', 'r', encoding='utf-8') as f:
    origin_lines = [l.strip() for l in f.readlines() if l.strip()]

df1 = pd.read_excel(r'D:\同传\4-已核对待评测-ASR+翻译+TTS数据\英文-已核对\en_36_9e9a1d04-28ce-47c2-9443-8d872a5c2e9d__e2e__en_zh__sentences.xlsx', header=None)
df2 = pd.read_excel(r'D:\同传\3-自研接口抓取-ASR+翻译+TTS\exported_logs-from自研接口原始数据\en2zh-94\en_36_f167c588-35b8-47c1-8210-56b77f8f9228__pipeline__en_zh\f167c588-35b8-47c1-8210-56b77f8f9228__pipeline__en_zh\en_36_f167c588-35b8-47c1-8210-56b77f8f9228__pipeline__en_zh__sentences.xlsx', header=None)

m1_asr_list = [str(x).strip() for x in df1.iloc[1:, 1].tolist()]
m1_trans_list = [str(x).strip() for x in df1.iloc[1:, 2].tolist()]
m2_asr_list = [str(x).strip() for x in df2.iloc[1:, 1].tolist()]
m2_trans_list = [str(x).strip() for x in df2.iloc[1:, 2].tolist()]

# ── Step 1: Semantic paragraphs ──
def build_paragraphs(lines, min_words=20):
    raw_groups = []
    current = []
    for line in lines:
        current.append(line)
        if line and line[-1] in '.?!':
            raw_groups.append(current)
            current = []
    if current:
        raw_groups.append(current)
    paragraphs = []
    buf = []
    buf_words = 0
    for group in raw_groups:
        text = ' '.join(group)
        wc = len(text.split())
        buf.extend(group)
        buf_words += wc
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

paragraphs = build_paragraphs(origin_lines, min_words=20)

# ── Step 2: Build segment-level char index ──
def build_seg_index(asr_list, trans_list):
    segs = []
    pos = 0
    for i, (a, t) in enumerate(zip(asr_list, trans_list)):
        start = pos
        pos += len(a)
        segs.append((start, pos, a, t))
        if i < len(asr_list) - 1:
            pos += 1  # separator
    full = ' '.join(asr_list)
    return full, segs

m1_full, m1_segs = build_seg_index(m1_asr_list, m1_trans_list)
m2_full, m2_segs = build_seg_index(m2_asr_list, m2_trans_list)

origin_full = ' '.join(origin_lines)

# ── Step 3: Word-level alignment origin → M2 ──
def word_positions(text):
    """Return list of (word, char_start, char_end)"""
    result = []
    for m in re.finditer(r'\S+', text):
        result.append((m.group(), m.start(), m.end()))
    return result

origin_words_pos = word_positions(origin_full)
m2_words_pos = word_positions(m2_full)

origin_words = [w for w, s, e in origin_words_pos]
m2_words = [w for w, s, e in m2_words_pos]

norm = lambda w: re.sub(r'[^a-z0-9]', '', w.lower())
sm = SequenceMatcher(None, [norm(w) for w in origin_words], [norm(w) for w in m2_words])

# Build origin_word_idx → m2_word_idx
o2m = {}
for tag, i1, i2, j1, j2 in sm.get_opcodes():
    if tag == 'equal':
        for k in range(i2 - i1):
            o2m[i1 + k] = j1 + k
    elif tag == 'replace':
        slen, tlen = i2 - i1, j2 - j1
        for k in range(slen):
            o2m[i1 + k] = j1 + int(k * tlen / slen)
    elif tag == 'delete':
        for k in range(i2 - i1):
            o2m[i1 + k] = j1

# ── Step 4: Map each paragraph → M2 segment indices ──
# Build origin line → char range
origin_line_ranges = []
pos = 0
for i, line in enumerate(origin_lines):
    origin_line_ranges.append((pos, pos + len(line)))
    pos += len(line) + (1 if i < len(origin_lines) - 1 else 0)

# For a paragraph (list of consecutive origin lines), find its char range in origin_full
def para_char_range(para_lines, start_line_idx):
    s = origin_line_ranges[start_line_idx][0]
    e = origin_line_ranges[start_line_idx + len(para_lines) - 1][1]
    return s, e

# Find which origin word indices fall in a char range
def words_in_range(char_start, char_end, words_pos):
    indices = []
    for i, (w, ws, we) in enumerate(words_pos):
        if ws >= char_start and we <= char_end:
            indices.append(i)
    return indices

# Find which M2 segments overlap a M2 char range
def segs_in_range(char_start, char_end, segs):
    """Return segment indices whose center falls in [char_start, char_end]"""
    indices = []
    for i, (ss, se, a, t) in enumerate(segs):
        center = (ss + se) / 2
        if center >= char_start and center <= char_end:
            indices.append(i)
    return indices

# ── Step 5: Build aligned table ──
results = []
origin_ptr = 0
m2_seg_used = set()

for para_idx, para_lines in enumerate(paragraphs):
    n = len(para_lines)
    o_start, o_end = para_char_range(para_lines, origin_ptr)
    
    # M1: direct 1:1 mapping
    m1_indices = list(range(origin_ptr, origin_ptr + n))
    m1_asr = ' '.join(m1_asr_list[i] for i in m1_indices)
    m1_trans = ' ‖ '.join(m1_trans_list[i] for i in m1_indices)
    
    # M2: word alignment → char range → segment indices
    o_word_indices = words_in_range(o_start, o_end, origin_words_pos)
    if o_word_indices:
        m2_word_start = o2m.get(o_word_indices[0], 0)
        m2_word_end = o2m.get(o_word_indices[-1], len(m2_words_pos) - 1)
        m2_char_start = m2_words_pos[m2_word_start][1]  # char start of first m2 word
        m2_char_end = m2_words_pos[min(m2_word_end, len(m2_words_pos)-1)][2]  # char end of last
        m2_seg_indices = segs_in_range(m2_char_start, m2_char_end, m2_segs)
    else:
        m2_seg_indices = []
    
    # Dedup: skip M2 segments already claimed by a previous paragraph
    m2_seg_indices = [i for i in m2_seg_indices if i not in m2_seg_used]
    m2_seg_used.update(m2_seg_indices)
    
    m2_asr = ' '.join(m2_segs[i][2] for i in m2_seg_indices)
    m2_trans = ' ‖ '.join(m2_segs[i][3] for i in m2_seg_indices)
    
    origin_text = ' '.join(para_lines)
    
    # Quality: compute word overlap between origin paragraph and M2 ASR
    o_norm = set(norm(w) for w in origin_text.split() if norm(w))
    m2_norm = set(norm(w) for w in m2_asr.split() if norm(w))
    overlap = len(o_norm & m2_norm) / max(len(o_norm), 1)
    
    results.append({
        'para_id': para_idx + 1,
        'origin_lines': f'{origin_ptr+1}-{origin_ptr+n}',
        'origin_word_count': len(origin_text.split()),
        'origin_text': origin_text,
        'm1_asr': m1_asr,
        'm1_translation': m1_trans,
        'm2_seg_ids': ','.join(str(i) for i in m2_seg_indices),
        'm2_asr': m2_asr,
        'm2_translation': m2_trans,
        'word_overlap': f'{overlap:.0%}',
    })
    
    origin_ptr += n

# ── Output ──
out_df = pd.DataFrame(results)
out_path = r'D:\POPO\AItester0227\AItester0227\aligned_v2.xlsx'
out_df.to_excel(out_path, index=False, engine='openpyxl')

print(f'Aligned {len(results)} paragraphs → {out_path}')
print(f'M2 segments used: {len(m2_seg_used)}/{len(m2_segs)}')
unused = set(range(len(m2_segs))) - m2_seg_used
if unused:
    print(f'M2 segments NOT mapped: {sorted(unused)}')

# ── Diagnostic: show all aligned paragraphs with quality ──
print('\n' + '='*100)
low_quality = []
for r in results:
    q = r['word_overlap']
    flag = ' [LOW]' if int(q.replace('%','')) < 50 else ''
    print(f"\n-- Para {r['para_id']} | lines {r['origin_lines']} | {r['origin_word_count']}w | overlap={q}{flag} --")
    print(f"  ORIGIN: {r['origin_text'][:200]}")
    print(f"  M2_ASR: {r['m2_asr'][:200]}")
    if int(q.replace('%','')) < 50:
        low_quality.append(r['para_id'])

print(f'\n\n=== SUMMARY ===')
print(f'Total paragraphs: {len(results)}')
print(f'Low quality (overlap<50%): {len(low_quality)} → paras {low_quality}')
overlaps = [int(r['word_overlap'].replace('%','')) for r in results]
print(f'Overlap stats: min={min(overlaps)}%, max={max(overlaps)}%, avg={sum(overlaps)/len(overlaps):.0f}%')
