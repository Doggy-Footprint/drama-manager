import json, glob, os
ROOT='/Users/hwansu/today/261031/bunkerkids/runs/park'
def files():
    return sorted(glob.glob(ROOT+'/*.jsonl'))+sorted(glob.glob(ROOT+'/baseline/*.jsonl'))
def turns(f):
    out=[]
    for line in open(f):
        try: out.append(json.loads(line))
        except Exception: pass
    return out
def load():
    rows=[]
    for f in files():
        name=os.path.relpath(f,ROOT)
        for i,d in enumerate(turns(f)):
            if 'trace' in d:
                n=d['entry']['n']
                for j,c in enumerate(d['trace'].get('calls') or []):
                    rows.append(dict(c, file=name, turn=n, idx=j))
            elif isinstance(d.get('usage'),dict) and 'label' in d['usage']:
                rows.append(dict(d['usage'], file=name, turn=d.get('n'), idx=0))
    return rows
