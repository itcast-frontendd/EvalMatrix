import pandas as pd

df = pd.read_excel(r'D:\POPO\AItester0227\AItester0227 - latest\aligned_output\row_14__590cec14-79f7-4bf9-927b-089c54252944__pipeline__ja_en__aligned.xlsx')
print(f'Columns: {list(df.columns)}')
print()
for i, row in df.iterrows():
    pid = row.iloc[0]
    asr = str(row.iloc[1])[:60]
    trans = str(row.iloc[2])[:60]
    txt = str(row.iloc[3])[:60]
    lines = row.iloc[4]
    conf = row.iloc[5]
    status = row.iloc[6]
    print(f'[{pid}] conf={conf} status={status} lines={lines}')
    print(f'  ASR: {asr}')
    print(f'  TXT: {txt}')
    print()
