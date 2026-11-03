from load import *
import numpy as np, collections, sys
rows=load()
LAB={'retrieve-events':'retrieve-world'}
for r in rows: r['L']=LAB.get(r['label'],r['label'])
def ols(X,y):
    X=np.column_stack([np.ones(len(y))]+X); b,*_=np.linalg.lstsq(X,y,rcond=None)
    pred=X@b; ss=((y-pred)**2).sum(); tot=((y-y.mean())**2).sum()
    return b, 1-ss/tot, y-pred
def fit(sub, with_unc=False, clean=False):
    y=np.array([r['seconds'] for r in sub],float); o=np.array([r['output'] for r in sub],float)
    u=np.array([r['input']-r['cached'] for r in sub],float)/1000
    X=[o]+([u] if with_unc else [])
    b,r2,res=ols(X,y); keep=np.ones(len(y),bool)
    if clean:
        for _ in range(3):
            mad=1.4826*np.median(np.abs(res-np.median(res)))
            keep=res< np.median(res)+3*mad   # 느린 쪽(재시도·서버 지연)만 뺀다
            b,r2,_=ols([x[keep] for x in X],y[keep])
            res=y-np.column_stack([np.ones(len(y))]+X)@b
    return b,r2,keep
def show(tag, sub):
    if len(sub)<8: return
    out=[]
    for clean in (False,True):
        b,r2,keep=fit(sub,False,clean)
        b2,r22,_=fit(sub,True,clean)
        out.append((clean,len(sub)-(~keep).sum(),(~keep).sum(),b,r2,b2,r22))
    for clean,n,drop,b,r2,b2,r22 in out:
        print(f"{tag:38s} {'clean' if clean else 'all  '} n={n:3d} drop={drop:2d}  a={b[0]:5.2f}s b={b[1]*1000:6.2f}ms/tok ({1/b[1] if b[1]>0 else float('nan'):6.1f} tok/s) R2={r2:.2f} | +uncached: a={b2[0]:5.2f} b={b2[1]*1000:5.2f}ms/tok c={b2[2]:5.2f}s/1k unc R2={r22:.2f}")
groups=collections.defaultdict(list)
early=lambda r: r['file'][:7]=='1103_15'   # 16:00 이전, 커밋 전 코드(retrieve-events)
for r in rows:
    groups[(r['L'],'all')].append(r)
    groups[(r['L'],'15h' if early(r) else '16h+')].append(r)
order=['drama-manager','character','retrieve-world','retrieve-details','extract-facts','dm-episode','baseline']
for l in order:
    for p in ['all','15h','16h+']:
        show(f"{l}/{p}", groups[(l,p)])
# 전체 풀링(라벨 공통)
show('POOLED json (no baseline)/16h+', [r for r in rows if r['L']!='baseline' and not early(r)])
