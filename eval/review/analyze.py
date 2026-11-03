import json,re,glob,statistics as st,collections,sys
R='/Users/hwansu/today/261031/bunkerkids/runs/park/'
RUNS={
 'E16-a':R+'1103_201404_overtime_overtime_persona-a.jsonl',
 'E16-b':R+'1103_201404_overtime_overtime_persona-b.jsonl',
 'E16-c':R+'1103_201404_overtime_overtime_persona-c.jsonl',
 'B16-a':R+'baseline/1103_201629_overtime_overtime_baseline-a.jsonl',
 'E50-a':R+'1103_204138_overtime_overtime_long-a.jsonl',
 'E50-b':R+'1103_204139_overtime_overtime_long-b.jsonl',
 'B50-a':R+'baseline/1103_204138_overtime_overtime_baseline-long-a.jsonl',
 'B50-b':R+'baseline/1103_204139_overtime_overtime_baseline-long-b.jsonl',
 # 수정 후(커밋 20c8ce1)
 'F16-a':R+'1103_212510_overtime_overtime_fix15-a.jsonl',
 'F16-b':R+'1103_212511_overtime_overtime_fix15-b.jsonl',
 'F16-c':R+'1103_212511_overtime_overtime_fix15-c.jsonl',
 'F50-a':R+'1103_212511_overtime_overtime_fix50-a.jsonl',
 'F50-b':R+'1103_212511_overtime_overtime_fix50-b.jsonl',
}
def load(k):
    rows=[json.loads(l) for l in open(RUNS[k])]
    if 'system' in rows[0]:
        T=[dict(n=r['n'],user=r['user'],reply=r['reply'],usage=r['usage']) for r in rows[1:]]
        return 'B',T,rows
    T=[dict(n=r['entry']['n'],user=r['entry']['user'],reply=r['entry']['reply'],narr=r['entry'].get('narration'),usage=r['entry']['usage'],tr=r['trace'],state=r['state']) for r in rows]
    return 'E',T,rows
def mins(c):
    h,m=c.split(':'); return int(h)*60+int(m)
HAN=r'[가-힣]'
def spoken(s): return re.sub(r'\([^)]*\)','',s).strip()
def toks(s):
    s=re.sub(r'[^\w\s가-힣]',' ',s); return s.split()
def ngrams(t,n=4): return [tuple(t[i:i+n]) for i in range(len(t)-n+1)]

DET={
 'ep1_commute_line':r'택시.*(퇴근길|창밖|길어요)|(퇴근길이 하루|하루 중에 제일)',
 'ep1_banana':r'바나나.*(위로|해장)|(위로|해장).*바나나|위로용',
 'ep1_one_year':r'딱 ?(1|일) ?년|(1|일) ?년만 더|버틸 거|1년.{0,12}(버티|더)',
 'ep2_three_minutes':r'3분|삼 ?분',
 'ep2_egg':r'반숙|노른자',
 'ep2_notebook':r'(노트|거긴|손바닥).{0,25}초고|초고.{0,10}노트|거긴 아직|손바닥으로 덮|넘겨보려|마지막 장.{0,10}(덮|가렸)',
 'ep2_dinner':r'제대로 된 (밥|저녁|식사)|컵라면 말고|PT 따면.{0,15}(밥|사)|밥 살게',
 'ep3_umbrella':r'우산.{0,30}(DH|일주일|빌려|빌린|갖다|돌려)|(DH|일주일|빌려|빌린|갖다|돌려).{0,30}우산',
 'ep3_run':r'뛰면 돼|비를 맞|뛰어가|목이 쉬|쉰 목|목소리가 쉬|감기',
 'ep3_just_peers':r'그냥 동기|동기예요|일 때문에요|친하더라|메신저로만',
}
DETP={k:re.compile(v) for k,v in DET.items()}
SEC_MAIN=re.compile(r'(택시)')
def conf_main(s):
    a='택시' in s and ('카피' in s or '문구' in s or '메인' in s) and re.search(r'허락|가져|적었|적어|썼|베꼈|따왔|인용|출처|훔',s)
    b=re.search(r'(채원 씨|택시).{0,12}(한|하신) 말(에서|이|을|로)',s) and re.search(r'카피|문장|가져|시작|썼|이 말|그 말',s)
    return bool(a or b)
def conf_main_loose(s):
    return conf_main(s) or bool('택시' in s and re.search(r'허락(도|을)? ?(없|안)|가져다 ?(써|썼|쓴)|출발점|(말|문장)(에서|으로) (시작|나왔|따)|따왔|베꼈',s))
def conf_taxi(s):
    return bool(re.search(r'40분|막차를? 놓쳐|놓쳐서 (걸어|집)|(불|창문).{0,10}켜질|창문에 불|걸어서 집',s))
RAIN=re.compile(r'(?<![가-힣])비(?:[가를는에도만]|(?![가-힣]))|빗|소나기')
POWER=re.compile(r'전원|불빛|(?<![가-힣])불(?:이|을|도)?(?![가-힣])|꺼(?!내|냈|낸)|형광등|어둠|캄캄')
CLAUSE_SKIP=re.compile(r'\d+분쯤|\d+분 (뒤|후)|\d+시간|한참|잠시 후|십 분|이십 분|삼십 분')
GEST={'볼펜':r'볼펜','노트':r'노트','입꼬리':r'입꼬리','식기 전':r'식기 전'}
CJK=re.compile(r'[぀-ヿ一-鿿]')

def formal(sent):
    s=re.sub(r'[\s"\'’”…~.?!]+$','',sent)
    return bool(re.search(r'(요|죠|니다|니까|세요|ㅂ시다)$',s))

def metrics(k):
    kind,T,raw=load(k); M={'turns':len(T)}
    reps=[t['reply'] for t in T]
    # 1 progress
    if kind=='E':
        c0=T[0]['tr']['clock_before']; c1=T[-1]['tr']['clock_after']
        tot=sum((mins(t['tr']['clock_after'])-mins(t['tr']['clock_before']))%1440 for t in T)
        adv=[(mins(t['tr']['clock_after'])-mins(t['tr']['clock_before']))%1440 for t in T]
        M['clock']=f'{c0}~{c1}'; M['min_total']=tot; M['min_per_turn']=round(tot/len(T),2)
        M['turns_adv_ge10']=sum(a>=10 for a in adv)
        M['max_adv']=max(adv)
        M['turns_adv_gt90']=sum(a>90 for a in adv)
        M['adv_list']=adv
        M['applied_minutes_max']=max([t['tr']['applied'].get('minutes') or 0 for t in T])
    else:
        M['clock']='—'; M['min_total']='—'; M['min_per_turn']='—'; M['turns_adv_ge10']='—'
    M['user_skip_turns']=sum(bool(CLAUSE_SKIP.search(t['user'])) for t in T)
    # 2 detail recall (string)
    first={}; proact={}
    anyp={}
    for d,p in DETP.items():
        for t in T:
            if p.search(t['reply']):
                first.setdefault(d,t['n'])
                if not p.search(t['user']): anyp.setdefault(d,t['n'])
    M['recall_str']={d:first.get(d) for d in DET}
    M['recall_str_n']=len(first)
    # proactive on first match
    byn={t['n']:t for t in T}
    M['recall_str_proactive_first']=[d for d,n in first.items() if not DETP[d].search(byn[n]['user'])]
    M['recall_str_proactive_any']=sorted(anyp)
    if kind=='E':
        fs={}
        for t in T:
            for u in t['state']['details']:
                if u['id'] in DET:
                    for x in u['uses']:
                        if x.get('realized') and x['by']=='도하': fs[u['id']]=min(fs.get(u['id'],999),x['turn'])
            for d in t['tr']['character'].get('used_details',[]):
                if d in DET: fs[d]=min(fs.get(d,999),t['n'])
        M['recall_struct']={d:fs.get(d) for d in DET}; M['recall_struct_n']=len(fs)
        # struct proactive: first turn user text lacks pattern
        M['recall_struct_proactive']=[d for d,n in fs.items() if not DETP[d].search(byn[n]['user'])]
        # DM via
    if kind=='E':
        dts=T[-1]['state']['details']
        tn=[d for d in dts if d.get('source')=='오늘 밤']
        def endturn(d):
            m_=re.search(r'T(\d+)(?:~T(\d+))?',d.get('origin') or ''); return int(m_.group(2) or m_.group(1)) if m_ else 0
        M['tonight_n']=len(tn)
        M['tonight_realized']=sum(any(u.get('realized') and u['turn']>endturn(d) for u in d['uses']) for d in tn)
        M['tonight_realized_uses']=sum(1 for d in tn for u in d['uses'] if u.get('realized'))
        qs=lambda sd:[re.sub(r'[\s\W]','',q) for q in re.findall(r'[‘“\'"]([^’”\'"]{4,})[’”\'"]',sd)]
        quo=0; quo_hit=0
        for d in tn:
            qq=qs(d['seed'])
            if not qq: continue
            quo+=1
            if any(any(q in re.sub(r'[\s\W]','',t['reply']) for q in qq) for t in T if t['n']>endturn(d)): quo_hit+=1
        M['tonight_quoted']=quo; M['tonight_quoted_hit']=quo_hit
        M['tonight_list']=[dict(id=d['id'],origin=d['origin'],kind=d.get('kind'),seed=d['seed'],use_turns=[u['turn'] for u in d['uses'] if u.get('realized')]) for d in tn]
        M['user_dh_details_n']=sum(1 for d in dts if d.get('source') in ('유저','도하'))
        M['episodes_n']=len(T[-1]['state']['memory']['episodes'])
    # 3 secrets
    sec={}
    for sid,fn in (('main_copy',conf_main),('taxi_wait',conf_taxi)):
        o={}
        o['text_conf_turns']=[t['n'] for t in T if fn(t['reply'])]
        if kind=='E':
            rv=[t['n'] for t in T if any(x['id']==sid for x in t['tr']['dm'].get('reveal',[]))]
            cf=[t['n'] for t in T if sid in t['tr']['character'].get('revealed',[])]
            o['dm_reveal']=rv; o['char_revealed']=cf
            op=rv[0] if rv else None
            o['leak_flag_turns']=[n for n in cf if op is None or n<op]
            tc=o['text_conf_turns']
            o['leak_text_turns']=[n for n in tc if op is None or n<op]
        sec[sid]=o
    lt=[t['n'] for t in T if conf_main_loose(t['reply'])]
    sec['main_copy']['loose_conf_turns']=lt
    if kind=='E':
        rv=sec['main_copy']['dm_reveal']; op=rv[0] if rv else None
        sec['main_copy']['loose_leak_turns']=[n for n in lt if op is None or n<op]
    M['secrets']=sec
    # 4 relationship
    if kind=='E':
        log=T[-1]['state']['relationship']['log']
        M['stage_changes']=[(l['turn'],l['text']) for l in log if l['what']=='stage']
        st0=T[0]['state']['relationship']['stats']; 
        init={'closeness':2,'trust':2,'flutter':1}; fin={x:v['value'] for x,v in T[-1]['state']['relationship']['stats'].items()}
        M['stats']=f"친밀 {init['closeness']}→{fin['closeness']}, 신뢰 {init['trust']}→{fin['trust']}, 설렘 {init['flutter']}→{fin['flutter']}"
        M['stat_changes']=sum(1 for l in log if l['what']=='stat')
        M['stat_changes_dm']=sum(1 for l in log if l['what']=='stat' and l['by']=='DM')
        M['stat_changes_event']=sum(1 for l in log if l['what']=='stat' and l['by']!='DM')
        M['final_stage']=T[-1]['state']['relationship']['stage']
        M['rel_rejects']=sum(1 for t in T for n in t['tr']['applied']['notes'] if not n['ok'] and n['text'].startswith('관계 거절'))
        M['promise_changes']=sum(1 for l in log if l['what']=='promise')
    # 5 events
    if kind=='E':
        ev=T[-1]['state']['world']['events']
        fired=[(e['id'],e['fired']['turn'],e['fired'].get('forced',False),e.get('core',False),e.get('added',False)) for e in ev if e.get('fired')]
        base=[f for f in fired if not f[4]]; M['events_fired']=len(base); M['events_added']=sum(1 for f in fired if f[4])
        M['events_list']=[(a,b,c) for a,b,c,d,e in base]
        M['core_forced']=[(a,b) for a,b,c,d,e in fired if c]
        # blocked then opened
        blocked={}
        for t in T:
            for b in t['tr']['retrieval']['event_blocked']: blocked.setdefault(b['id'],[]).append((t['n'],b['why']))
        firedmap={a:b for a,b,*_ in fired}
        bo=[]
        for i,l in blocked.items():
            if i in firedmap and firedmap[i]>l[0][0]:
                last=[n for n,_ in l if n<firedmap[i]][-1]
                bo.append((i,l[0][0],last,firedmap[i],l[0][1]))
        M['blocked_ids']=sorted(blocked); M['blocked_then_opened']=bo
        M['blocked_never_opened']=sorted(i for i in blocked if i not in firedmap)
        # core reflect
        refl={}
        for a,n,fo,core,added in fired:
            if core:
                p=RAIN if a=='rain_starts' else POWER
                tt=byn[n]; nxt=byn.get(n+1)
                w=[byn[x] for x in (n,n+1,n+2) if x in byn]
                refl[a]=dict(turn=n,forced=fo,narr=bool(p.search(tt['narr'] or '')),reply=bool(p.search(tt['reply'])),
                    within2=any(p.search((x['narr'] or '')+' '+x['reply']) for x in w))
        M['core_reflect']=refl
        M['core_unfired']=[e['id'] for e in ev if e.get('core') and not e.get('fired')]
    M['rain_reply_turns']=sum(bool(RAIN.search(r)) for r in reps)
    M['power_reply_turns']=sum(bool(POWER.search(r)) for r in reps)
    # 6 code rejections
    if kind=='E':
        cat=collections.Counter()
        for t in T:
            for n in t['tr']['applied']['notes']:
                if n['ok']: continue
                x=n['text']
                if x.startswith('떡밥 거절'): cat['후보 밖 떡밥 거절']+=1
                elif x.startswith('충전 거절'): cat['충전 거절']+=1
                elif x.startswith('관계 거절'): cat['관계 거절: 수치 상한(이미 N)' if '이미' in x else '관계 거절: 기타']+=1
                elif x.startswith('이벤트 거절'): cat['이벤트 후보 밖 거절']+=1
                elif x.startswith('세계 확장 안 받음'): cat['세계 확장 거절']+=1
                elif x.startswith('시간 고침'): cat['(시간 보정: 한 턴 상한 초과를 깎음, 거절 아님)']+=1
                elif x.startswith('시작 턴'): cat['(시간 보정: 시작 턴 +N분→0, 거절 아님)']+=1
                else: cat['기타: '+x[:25]]+=1
            for n in t['tr']['character'].get('character_applied',[]):
                if not n['ok']: cat['캐릭터 응답 적용 거절']+=1
        M['rejects']=dict(cat)
        M['rejects_total_excl_timefix']=sum(v for k2,v in cat.items() if not k2.startswith('(시간'))
        gt=[(t['n'],g) for t in T for g in t['tr']['character'].get('_guard',[])]
        M['guard_lang_turns']=len({n for n,g in gt if '한국어가 아닌' in g['text'] or '남은 글자' in g['text']})
        M['guard_secret_turns']=len({n for n,g in gt if '잠긴 비밀' in g['text']})
        M['guard_secret_failed']=sum(1 for n,g in gt if '다시 써도 잠긴 비밀' in g['text'])
        M['guard_turns']=sum(1 for t in T if t['tr']['character'].get('_guard'))
        M['guard_rewrite_attempts']=sum(1 for t in T for g in t['tr']['character'].get('_guard',[]) if not g['ok'])
    M['cjk_reply_turns']=[t['n'] for t in T if CJK.search(t['reply'])]
    # 7 repetition
    def rep(texts):
        hit=0;tot=0;ng_rep=0;ng_tot=0
        hist=[]
        for i,s in enumerate(texts):
            g=set(ngrams(toks(s)))
            prev=set().union(*hist[-3:]) if hist else set()
            if i>0:
                tot+=1
                if g&prev: hit+=1
                ng_tot+=len(g); ng_rep+=len(g&prev)
            hist.append(g)
        return round(100*hit/max(tot,1),1), round(100*ng_rep/max(ng_tot,1),1)
    M['rep_full']=rep(reps); M['rep_spoken']=rep([spoken(r) for r in reps])
    M['gesture']={g:round(100*sum(bool(re.search(p,r)) for r in reps)/len(reps),1) for g,p in GEST.items()}
    M['gesture_any']=round(100*sum(any(re.search(p,r) for p in GEST.values()) for r in reps)/len(reps),1)
    M['paren_turns']=round(100*sum('(' in r for r in reps)/len(reps),1)
    # 8 address / register
    sp=[spoken(r) for r in reps]
    call=collections.Counter()
    for s in sp:
        call['채원 씨']+=len(re.findall(r'(?<!윤)채원 씨',s))
        call['윤채원 씨']+=len(re.findall(r'윤채원 씨',s))
        call['채원아/야']+=len(re.findall(r'채원[아야](?![가-힣])',s))
        call['채원(맨이름·기타)']+=len(re.findall(r'채원(?!\s*씨)(?![아야])(?![가-힣])',s))
    M['address']=dict(call)
    M['address_turns']={'채원 씨':sum(bool(re.search(r'(?<!윤)채원 씨',s)) for s in sp),'채원아/야':sum(bool(re.search(r'채원[아야](?![가-힣])',s)) for s in sp)}
    sents=[x for s in sp for x in re.split(r'(?<=[.?!…])\s+',s) if re.search(HAN,x)]
    # strip quotes
    sents=[x.strip() for x in sents]
    nf=sum(1 for x in sents if formal(x)); 
    M['sent_n']=len(sents); M['banmal_pct']=round(100*(len(sents)-nf)/max(len(sents),1),1)
    M['banmal_turns_pct']=round(100*sum(any(not formal(x) for x in re.split(r'(?<=[.?!…])\s+',s) if re.search(HAN,x)) for s in sp)/len(sp),1)
    # 10 cost
    inp=[t['usage']['input'] for t in T]; ca=[t['usage']['cached'] for t in T]; out=[t['usage']['output'] for t in T]
    M['in_avg']=round(st.mean(inp)); M['cached_avg']=round(st.mean(ca)); M['out_avg']=round(st.mean(out))
    M['cache_hit_pct']=round(100*sum(ca)/sum(inp),1)
    if kind=='E':
        lat=[]; lab=collections.defaultdict(lambda:[0,0,0,0.0])
        for t in T:
            cs=t['tr']['calls']
            par=max([c['seconds'] for c in cs if c['label'] in ('extract-facts','retrieve-world','retrieve-details')] or [0])
            dm=sum(c['seconds'] for c in cs if c['label']=='drama-manager')
            ch=sum(c['seconds'] for c in cs if c['label'] in ('character','character-guard'))
            lat.append(par+dm+ch)
            for c in cs:
                l=lab[c['label']]; l[0]+=1;l[1]+=c['input'];l[2]+=c['output'];l[3]+=c['seconds']
        M['lat_avg']=round(st.mean(lat),1); M['lat_med']=round(st.median(lat),1); M['lat_max']=round(max(lat),1)
        M['lat_serial_avg']=round(st.mean(t['usage']['seconds'] for t in T),1)
        n=len(T)
        M['labels']={k2:dict(calls=v[0],in_k_per_turn=round(v[1]/n/1000,2),out_k_per_turn=round(v[2]/n/1000,2),sec_per_call=round(v[3]/v[0],1)) for k2,v in lab.items()}
    else:
        sec_=[t['usage']['seconds'] for t in T]
        M['lat_avg']=round(st.mean(sec_),1); M['lat_med']=round(st.median(sec_),1); M['lat_max']=round(max(sec_),1)
        M['labels']={'baseline':dict(calls=len(T),in_k_per_turn=round(sum(inp)/len(T)/1000,2),out_k_per_turn=round(sum(out)/len(T)/1000,2),sec_per_call=round(st.mean(sec_),1))}
    M['bucket_in']={}
    for b in range(0,len(T),10):
        seg=T[b:b+10]; 
        M['bucket_in'][f'T{b+1}-{b+len(seg)}']=dict(input=round(st.mean(x['usage']['input'] for x in seg)),cached=round(st.mean(x['usage']['cached'] for x in seg)),output=round(st.mean(x['usage']['output'] for x in seg)),lat=round(st.mean([ (lambda cs:max([c['seconds'] for c in cs if c['label'] in ('extract-facts','retrieve-world','retrieve-details')] or [0])+sum(c['seconds'] for c in cs if c['label'] in ('drama-manager','character','character-guard')))(x['tr']['calls']) if kind=='E' else x['usage']['seconds'] for x in seg]),1))
    M['replies_len_avg']=round(st.mean(len(r) for r in reps))
    return M

if __name__=='__main__':
    out={k:metrics(k) for k in RUNS}
    json.dump(out,open(sys.argv[1] if len(sys.argv)>1 else 'metrics.json','w'),ensure_ascii=False,indent=1)
    print('ok')
