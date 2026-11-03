from load import *
import statistics as st, collections
BEFORE=['1103_171545_surgeon_persona-a.jsonl','1103_171546_surgeon_persona-b.jsonl']
AFTER=['1103_180227_surgeon_persona-a.jsonl','1103_180226_surgeon_persona-b.jsonl']
rows=load()
def sel(fs,l): return [r for r in rows if r['file'] in fs and r['label']==l]
def m(x): return st.mean(x) if x else float('nan')
print('label             nB  nA  out B→A (Δ%)        med out B→A   sec B→A (Δ%)        med sec B→A    in B→A   uncached B→A')
for l in ['drama-manager','character','retrieve-world','retrieve-details','extract-facts','dm-episode']:
    B=sel(BEFORE,l); A=sel(AFTER,l)
    ob,oa=m([r['output'] for r in B]),m([r['output'] for r in A])
    sb,sa=m([r['seconds'] for r in B]),m([r['seconds'] for r in A])
    mob,moa=st.median([r['output'] for r in B]),st.median([r['output'] for r in A])
    msb,msa=st.median([r['seconds'] for r in B]),st.median([r['seconds'] for r in A])
    ib,ia=m([r['input'] for r in B]),m([r['input'] for r in A])
    ub,ua=m([r['input']-r['cached'] for r in B]),m([r['input']-r['cached'] for r in A])
    print(f"{l:17s} {len(B):3d} {len(A):3d}  {ob:5.0f}→{oa:5.0f} ({(oa/ob-1)*100:+5.0f}%)  {mob:5.0f}→{moa:5.0f}  {sb:5.2f}→{sa:5.2f} ({(sa/sb-1)*100:+5.0f}%)  {msb:5.2f}→{msa:5.2f}  {ib:6.0f}→{ia:6.0f} {ub:6.0f}→{ua:6.0f}")
# per-turn
def per_turn(fs):
    out=[]
    for f in fs:
        for d in turns(ROOT+'/'+f):
            cs=d['trace']['calls']
            by=collections.defaultdict(list)
            for c in cs: by[c['label']].append(c)
            ret=[c['seconds'] for c in cs if c['label'] in ('retrieve-world','retrieve-details','extract-facts','retrieve-events')]
            crit=(max(ret) if ret else 0)+sum(c['seconds'] for c in by['drama-manager'])+sum(c['seconds'] for c in by['character']+by['character-guard'])
            out.append(dict(tot_out=sum(c['output'] for c in cs if c['label']!='baseline'),
                             crit=crit, ret=max(ret) if ret else 0,
                             dm=sum(c['seconds'] for c in by['drama-manager']),
                             ch=sum(c['seconds'] for c in by['character']+by['character-guard']),
                             ep=sum(c['seconds'] for c in by['dm-episode'])))
    return out
for name,fs in [('before',BEFORE),('after',AFTER)]:
    t=per_turn(fs)
    print(name, 'turns',len(t),'out/turn',round(m([x['tot_out'] for x in t])),'crit mean',round(m([x['crit'] for x in t]),2),'median',round(st.median([x['crit'] for x in t]),2),
          'retr(max)',round(m([x['ret'] for x in t]),2),'dm',round(m([x['dm'] for x in t]),2),'char(all turns)',round(m([x['ch'] for x in t]),2),'+episode/turn',round(m([x['ep'] for x in t]),2))
