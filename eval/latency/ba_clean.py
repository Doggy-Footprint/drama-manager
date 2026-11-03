from load import *
import statistics as st, json
FIT={'drama-manager':(1.44,0.01052),'character':(1.30,0.01362),'retrieve-world':(1.83,0.00987),'retrieve-details':(1.77,0.01140),'extract-facts':(1.48,0.01103)}
B=['1103_171545_surgeon_persona-a.jsonl','1103_171546_surgeon_persona-b.jsonl']; A=['1103_180227_surgeon_persona-a.jsonl','1103_180226_surgeon_persona-b.jsonl']
rows=load()
def ok(r):  # 회귀에서 쓴 것과 같은 느린 쪽 이상치 기준 근사: 예측보다 4초 이상 느리면 제외
    a,b=FIT[r['label']]; return r['seconds']-(a+b*r['output'])<4
for l in FIT:
    xb=[r for r in rows if r['file'] in B and r['label']==l and ok(r)]; xa=[r for r in rows if r['file'] in A and r['label']==l and ok(r)]
    ob,oa=st.mean(r['output'] for r in xb),st.mean(r['output'] for r in xa); sb,sa=st.mean(r['seconds'] for r in xb),st.mean(r['seconds'] for r in xa)
    a,b=FIT[l]; pred=(b*(oa-ob))/sb*100
    print(f"{l:17s} clean nB={len(xb)} nA={len(xa)} out {ob:.0f}→{oa:.0f} ({(oa/ob-1)*100:+.0f}%)  sec {sb:.2f}→{sa:.2f} ({(sa/sb-1)*100:+.0f}%)  model-expected sec Δ {pred:+.0f}%")
