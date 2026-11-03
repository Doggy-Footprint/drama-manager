from load import *
import statistics as st, collections
rows=load()
g=collections.defaultdict(list)
for r in rows: g[(r['file'],r['label'])].append(r)
labs=['drama-manager','character','retrieve-world','retrieve-events','retrieve-details','extract-facts','dm-episode','baseline']
print('file'.ljust(48),'label'.ljust(17),'n   out_mean out_med  sec_mean sec_med  in_mean  uncached')
for f in sorted(set(r['file'] for r in rows)):
    for l in labs:
        x=g.get((f,l))
        if not x: continue
        o=[r['output'] for r in x]; s=[r['seconds'] for r in x]; i=[r['input'] for r in x]; u=[r['input']-r['cached'] for r in x]
        print(f[:47].ljust(48), l.ljust(17), str(len(x)).ljust(3), f"{st.mean(o):7.0f} {st.median(o):6.0f}  {st.mean(s):7.2f} {st.median(s):6.2f}  {st.mean(i):7.0f} {st.mean(u):7.0f}")
