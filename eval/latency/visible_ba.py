from load import *
import json, statistics as st, tiktoken
enc=tiktoken.get_encoding('o200k_base')
for tag,fs in [('before',['1103_171545_surgeon_persona-a.jsonl','1103_171546_surgeon_persona-b.jsonl']),('after',['1103_180227_surgeon_persona-a.jsonl','1103_180226_surgeon_persona-b.jsonl'])]:
    V=[];O=[]
    for f in fs:
        for d in turns(ROOT+'/'+f):
            dm=[c for c in d['trace']['calls'] if c['label']=='drama-manager']
            V.append(len(enc.encode(json.dumps(d['trace']['dm'],ensure_ascii=False,separators=(',',':'))))); O.append(dm[0]['output'])
    print(tag,'DM visible(compact json, o200k)',round(st.mean(V)),'recorded',round(st.mean(O)),'hidden≈',round(st.mean(O)-st.mean(V)))
