# 시간대별 서버 속도: 파일마다 (seconds - a)/output 의 중앙값 → 실효 tok/s, 그리고 a 고정 시 잔차 중앙값
from load import *
import statistics as st, collections
FIT={'drama-manager':(1.44,0.01052),'character':(1.30,0.01362),'retrieve-world':(1.83,0.00987),'retrieve-events':(1.83,0.00987),
     'retrieve-details':(1.77,0.01140),'extract-facts':(1.48,0.01103),'dm-episode':(1.56,0.01113)}
rows=[r for r in load() if r['label'] in FIT]
g=collections.defaultdict(list)
for r in rows: g[r['file']].append(r)
for f,x in sorted(g.items()):
    res=[r['seconds']-(FIT[r['label']][0]+FIT[r['label']][1]*r['output']) for r in x]
    dm=[r for r in x if r['label']=='drama-manager']
    tps=st.median([r['output']/(r['seconds']-1.44) for r in dm if r['seconds']>2])
    print(f"{f[:40]:41s} n={len(x):3d} resid median={st.median(res):+5.2f}s mean={st.mean(res):+5.2f}s  DM eff tok/s(median)={tps:5.0f}")
