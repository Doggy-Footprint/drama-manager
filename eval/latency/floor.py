from load import *
import statistics as st
rows=load()
for lo,hi in [(0,30),(31,80),(81,150)]:
    x=[r['seconds'] for r in rows if lo<=r['output']<=hi and r['label']!='baseline']
    if x: print(f"output {lo}-{hi}: n={len(x)} median={st.median(x):.2f}s p25={sorted(x)[len(x)//4]:.2f} min={min(x):.2f}")
x=[(r['label'],r['output'],r['seconds']) for r in rows if r['output']<=30]
import collections; print(collections.Counter(l for l,_,_ in x))
