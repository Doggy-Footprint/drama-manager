# DM·검색기의 보이는 JSON 길이(o200k 토큰) 대 기록된 output_tokens → 추론 토큰 몫 추정
from load import *
import json, statistics as st, tiktoken
enc=tiktoken.get_encoding('o200k_base')
res={}
for f in files():
    if '/baseline/' in f: continue
    for d in turns(f):
        tr=d['trace']; calls=tr['calls']
        dm=[c for c in calls if c['label']=='drama-manager']
        ch=[c for c in calls if c['label']=='character']
        if dm and 'dm' in tr:
            v=len(enc.encode(json.dumps(tr['dm'],ensure_ascii=False)))
            res.setdefault('drama-manager',[]).append((v,dm[0]['output']))
        if len(ch)==1 and 'character' in tr:
            c={k:v for k,v in tr['character'].items() if not k.startswith('_')}
            v=len(enc.encode(json.dumps(c,ensure_ascii=False)))
            res.setdefault('character',[]).append((v,ch[0]['output']))
for k,x in res.items():
    vis=[a for a,b in x]; out=[b for a,b in x]
    print(k,'n',len(x),'visible(o200k) mean',round(st.mean(vis)),'recorded output mean',round(st.mean(out)),'→ hidden ≈',round(st.mean(out)-st.mean(vis)),f"({(1-st.mean(vis)/st.mean(out))*100:.0f}%)")
