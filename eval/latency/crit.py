from load import *
import statistics as st, collections, os
# regress.py 의 16h+ clean 적합값 (a 초, b 초/토큰)
FIT={'drama-manager':(1.44,0.01052),'character':(1.30,0.01362),'character-guard':(1.30,0.01362),
     'retrieve-world':(1.83,0.00987),'retrieve-details':(1.77,0.01140),'extract-facts':(1.48,0.01103),'dm-episode':(1.56,0.01113)}
RET=('retrieve-world','retrieve-details','extract-facts')
def crit(calls, f=lambda c:c['seconds']):
    by=collections.defaultdict(list)
    for c in calls: by[c['label']].append(c)
    r=max([f(c) for c in calls if c['label'] in RET] or [0])
    d=sum(f(c) for c in by['drama-manager']); ch=sum(f(c) for c in by['character']+by['character-guard'])
    return r,d,ch
def run(sel, tag):
    T=[];F=[];H=[];HD=[];M=[]
    for f in files():
        name=os.path.relpath(f,ROOT)
        if '/' in name or not sel(name): continue
        for d in turns(f):
            cs=d['trace']['calls']
            if not any(c['label'] in RET for c in cs): continue
            r,dm,ch=crit(cs); T.append((r,dm,ch))
            # 고정 지연: 각 단계의 a (검색 단계는 실제로 가장 늦은 호출의 a)
            slow=max([c for c in cs if c['label'] in RET], key=lambda c:c['seconds'])
            fx=FIT[slow['label']][0]+FIT['drama-manager'][0]*sum(c['label']=='drama-manager' for c in cs)+FIT['character'][0]*sum(c['label'] in('character','character-guard') for c in cs)
            F.append(fx)
            # 출력 절반: 실제 초에서 b*out/2를 뺀다(잔차 유지)
            half=lambda c: c['seconds']-FIT.get(c['label'],(0,0))[1]*c['output']/2
            H.append(sum(crit(cs,half)))
            halfdm=lambda c: half(c) if c['label']=='drama-manager' else c['seconds']
            HD.append(sum(crit(cs,halfdm)))
            # 모델만으로 예측한 턴 시간
            M.append(sum(crit(cs,lambda c: FIT.get(c['label'],(0,0))[0]+FIT.get(c['label'],(0,0))[1]*c['output'])))
    tot=[sum(t) for t in T]
    print(f"{tag}: turns={len(T)} crit mean={st.mean(tot):.2f}s median={st.median(tot):.2f} | retr(max)={st.mean(t[0] for t in T):.2f} DM={st.mean(t[1] for t in T):.2f} char={st.mean(t[2] for t in T):.2f} | model-pred={st.mean(M):.2f}")
    print(f"   fixed latency on path mean={st.mean(F):.2f}s ({st.mean(F)/st.mean(tot)*100:.0f}%) | all outputs halved → {st.mean(H):.2f}s ({(st.mean(H)/st.mean(tot)-1)*100:+.0f}%), median {st.median(H):.2f} | DM only halved → {st.mean(HD):.2f}s ({(st.mean(HD)/st.mean(tot)-1)*100:+.0f}%)")
run(lambda n: not n.startswith('1103_15'), 'all 16h+ runs')
run(lambda n: n.startswith(('1103_171545','1103_171546')), 'surgeon before f8acd89')
run(lambda n: n.startswith(('1103_180226','1103_180227')), 'surgeon after f8acd89')
run(lambda n: 'overtime' in n, 'overtime (latest code)')
