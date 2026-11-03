import json
m=json.load(open('metrics.json'))
G=[('엔진 16턴 (a/b/c)',['E16-a','E16-b','E16-c']),('기준선 16턴',['B16-a']),('엔진 50턴 (a/b)',['E50-a','E50-b']),('기준선 50턴 (a/b)',['B50-a','B50-b']),('엔진 16턴 (a/b/c) 수정 후',['F16-a','F16-b','F16-c']),('엔진 50턴 (a/b) 수정 후',['F50-a','F50-b'])]
def cell(ks,f):
    v=[f(m[k]) for k in ks]; return ' / '.join(str(x) for x in v)
E=lambda f,d='—':(lambda x: f(x) if 'stats' in x else d)
rows=[]
def add(name,f,eng_only=False):
    rows.append((name,[cell(ks,(lambda x,f=f:f(x)) if not(eng_only and ks[0][0]=='B') else (lambda x:'—')) for _,ks in G]))
def addF(name,f): rows.append((name,[cell(ks,(lambda x,f=f:f(x)) if ks[0][0]=='F' else (lambda x:'—')) for _,ks in G]))
def add_(name,f): rows.append((name,[cell(ks,(lambda x,f=f:f(x)) if ks[0][0] in 'EF' else (lambda x:'—')) for _,ks in G]))
add('1 턴 수',lambda x:x['turns'])
add_('1 게임 시각 범위',lambda x:x['clock'])
add_('1 턴당 평균 경과 분',lambda x:x['min_per_turn'])
add_('1 경과 10분 이상 턴 수',lambda x:x['turns_adv_ge10'])
add_('1 경과 90분 초과 턴 수',lambda x:x['turns_adv_gt90'])
add_('1 한 턴 최대 경과 분',lambda x:x['max_adv'])
add('1 유저가 시간 건너뛴 턴(입력에 "N분쯤/시간" 등)',lambda x:x['user_skip_turns'])
add('2 회수 디테일 수 /10 (대사 문자열 매칭)',lambda x:x['recall_str_n'])
add_('2 회수 디테일 수 /10 (엔진 구조 기록: realize·used_details)',lambda x:x['recall_struct_n'])
add('2 먼저 꺼낸 회수 수 (첫 회수 턴 기준, 문자열)',lambda x:len(x['recall_str_proactive_first']))
addF('2 "오늘 밤" 디테일 수(에피소드에서 뽑힌 것)',lambda x:x['tonight_n'])
addF('2 "오늘 밤" 디테일 중 만든 뒤 도하가 실제로 쓴(realized) 수',lambda x:x['tonight_realized'])
addF('2 "오늘 밤" 디테일 중 따옴표 인용 있는 수 / 그중 이후 대사에 그대로 나온 수',lambda x:f"{x['tonight_quoted']}/{x['tonight_quoted_hit']}")
addF('2 에피소드(묶음) 수',lambda x:x['episodes_n'])
add_('2 먼저 꺼낸 회수 수 (구조 기록 기준)',lambda x:len(x['recall_struct_proactive']))
add('3 main_copy 털어놓은 턴(대사 매칭)',lambda x:x['secrets']['main_copy']['text_conf_turns'] or '-')
add_('3 main_copy DM 열림 턴',lambda x:x['secrets']['main_copy']['dm_reveal'] or '-')
add_('3 main_copy 털어놓은 턴(느슨한 대사 매칭: 허락 없이/출발점/가져다 써 포함)',lambda x:x['secrets']['main_copy']['loose_conf_turns'] or '-')
add_('3 main_copy 캐릭터 revealed 플래그 턴',lambda x:x['secrets']['main_copy']['char_revealed'] or '-')
add_('3 main_copy 열리기 전 새어 나감(플래그 턴 수 / 대사매칭 턴 수)',lambda x:f"{len(x['secrets']['main_copy']['leak_flag_turns'])}/{len(x['secrets']['main_copy']['leak_text_turns'])}")
add_('3 main_copy 열리기 전 새어 나감(느슨한 매칭 턴 수)',lambda x:len(x['secrets']['main_copy']['loose_leak_turns']))
addF('3 잠긴 비밀 다시 쓰기 가드 발동 턴(수정 후에만 있는 가드)',lambda x:x['guard_secret_turns'])
add('3 taxi_wait 털어놓은 턴(대사 매칭)',lambda x:x['secrets']['taxi_wait']['text_conf_turns'] or '-')
add_('3 taxi_wait DM 열림 턴',lambda x:x['secrets']['taxi_wait']['dm_reveal'] or '-')
add_('3 taxi_wait 열리기 전 새어 나감(대사매칭 턴 수)',lambda x:len(x['secrets']['taxi_wait']['leak_text_turns']))
add_('4 단계 변화 턴',lambda x:'; '.join(f"T{t} {s}" for t,s in x['stage_changes']) or '없음')
add_('4 수치 시작→끝',lambda x:x['stats'])
add_('4 수치 변화 횟수(DM/이벤트)',lambda x:f"{x['stat_changes']}({x['stat_changes_dm']}/{x['stat_changes_event']})")
add_('4 코드가 거절한 관계 갱신',lambda x:x['rel_rejects'])
add_('5 발생 이벤트 수(core 강제 포함, x_event 제외)',lambda x:x['events_fired'])
add_('5 강제 발동 core 수(rain_starts)',lambda x:len(x['core_forced']))
add_('5 막혔다 나중에 열린 이벤트',lambda x:'; '.join(f"{a}(막힘 T{b}~{c}, 열림 T{d})" for a,b,c,d,_ in x['blocked_then_opened']) or '없음')
add_('5 끝까지 막힌 이벤트',lambda x:','.join(x['blocked_never_opened']) or '-')
add_('5 core 이벤트 지문·대사 반영(같은 턴 지문/대사, +2턴 이내)',lambda x:'; '.join(f"{a}:T{v['turn']}{'강제' if v['forced'] else ''} 지문{'O' if v['narr'] else 'X'}대사{'O' if v['reply'] else 'X'}+2턴{'O' if v['within2'] else 'X'}" for a,v in x['core_reflect'].items()) or '-')
add_('6 코드 거절 합계(시간보정 제외)',lambda x:x['rejects_total_excl_timefix'])
add_('6  - 후보 밖 떡밥',lambda x:x['rejects'].get('후보 밖 떡밥 거절',0))
add_('6  - 충전 거절',lambda x:x['rejects'].get('충전 거절',0))
add_('6  - 관계 수치 상한',lambda x:x['rejects'].get('관계 거절: 수치 상한(이미 N)',0))
add_('6  - 이벤트 후보 밖',lambda x:x['rejects'].get('이벤트 후보 밖 거절',0))
add_('6  - 세계 확장 거절',lambda x:x['rejects'].get('세계 확장 거절',0))
add_('6  - 기타(위치·channel 불일치, 효과)',lambda x:sum(v for k,v in x['rejects'].items() if k.startswith('기타')))
add_('6 언어 가드레일 발동 턴(재작성 시도)',lambda x:f"{x['guard_turns']}({x['guard_rewrite_attempts']})")
addF('6  - 언어 가드레일만 발동한 턴 / 잠긴 비밀 가드 발동 턴',lambda x:f"{x['guard_lang_turns']}/{x['guard_secret_turns']}")
add('6 최종 대사에 한자·가나 섞인 턴',lambda x:len(x['cjk_reply_turns']))
add('7 대사 4-gram 반복: 최근3턴과 겹치는 턴 비율 % (괄호 제거 대사)',lambda x:x['rep_spoken'][0])
add('7 대사 4-gram 반복: 같은 기준 4-gram 비율 %',lambda x:x['rep_spoken'][1])
add('7 전체(몸짓 포함) 4-gram 반복 턴 비율 %',lambda x:x['rep_full'][0])
for g in ('볼펜','노트','입꼬리','식기 전'): add(f'7 "{g}" 등장 턴 %',lambda x,g=g:x['gesture'][g])
add('7 위 4개 중 하나라도 등장한 턴 %',lambda x:x['gesture_any'])
add('8 "채원 씨" 횟수(대사 본문)',lambda x:x['address']['채원 씨'])
add('8 "채원아/야" 횟수',lambda x:x['address']['채원아/야'])
add('8 이름만("채원") 횟수',lambda x:x['address']['채원(맨이름·기타)'])
add('8 반말 문장 비율 %',lambda x:x['banmal_pct'])
add('8 반말 문장이 하나라도 있는 턴 %',lambda x:x['banmal_turns_pct'])
add('10 턴당 입력 토큰(k)',lambda x:round(x['in_avg']/1000,1))
add('10 턴당 캐시 토큰(k)',lambda x:round(x['cached_avg']/1000,1))
add('10 턴당 출력 토큰(k)',lambda x:round(x['out_avg']/1000,2))
add('10 캐시 적중률 %',lambda x:x['cache_hit_pct'])
add('10 턴 지연 s 평균(엔진=임계경로, 기준선=호출)',lambda x:x['lat_avg'])
add('10 턴 지연 s 중앙/최대',lambda x:f"{x['lat_med']}/{x['lat_max']}")
add_('10 (참고) 호출 시간 단순 합 s',lambda x:x['lat_serial_avg'])
hdr='| 지표 | '+' | '.join(g for g,_ in G)+' |\n|---|'+'---|'*len(G)+'\n'
md='# 야근 이야기 엔진 vs 기준선 정량 비교 (수정 전·후)\n\n@@KEY@@\n## 전체 지표\n\n'+hdr+''.join(f'| {n} | '+' | '.join(c)+' |\n' for n,c in rows)

def kt():
    P15=G[0][1];P50=G[2][1];Q15=G[4][1];Q50=G[5][1]
    cols=['15턴 수정 전 (a/b/c)','15턴 수정 후 (a/b/c)','50턴 수정 전 (a/b)','50턴 수정 후 (a/b)']
    grp=[P15,Q15,P50,Q50]
    R=[]
    def r(name,f,pre=True):
        R.append((name,[cell(ks,f) for ks in grp]))
    r('턴 수',lambda x:x['turns'])
    r('main_copy DM이 연 턴',lambda x:x['secrets']['main_copy']['dm_reveal'] or '-')
    r('main_copy 털어놓은 턴(느슨한 대사 매칭)',lambda x:x['secrets']['main_copy']['loose_conf_turns'] or '-')
    r('main_copy 열리기 전 새어 나감(플래그 턴 수 / 느슨한 대사 턴 수)',lambda x:f"{len(x['secrets']['main_copy']['leak_flag_turns'])}/{len(x['secrets']['main_copy']['loose_leak_turns'])}")
    R.append(('잠긴 비밀 다시 쓰기 가드 발동 턴',['(가드 없음)']+[cell(Q15,lambda x:x['guard_secret_turns'])]+['(가드 없음)']+[cell(Q50,lambda x:x['guard_secret_turns'])]))
    r('rain_starts 강제 발동(턴)',lambda x:','.join(f"T{t}" for a,t in x['core_forced'] if a=='rain_starts') or '없음')
    r('core 같은 턴 지문 반영 rain / power (O,X,-)',lambda x:'/'.join(('O' if x['core_reflect'][e]['narr'] else 'X') if e in x['core_reflect'] else '-' for e in ('rain_starts','power_off')))
    r('core 같은 턴 대사 반영 rain / power',lambda x:'/'.join(('O' if x['core_reflect'][e]['reply'] else 'X') if e in x['core_reflect'] else '-' for e in ('rain_starts','power_off')))
    r('턴당 평균 경과 분',lambda x:x['min_per_turn'])
    r('게임 시각 범위',lambda x:x['clock'])
    r('90분 초과 턴 수 / 한 턴 최대 분',lambda x:f"{x['turns_adv_gt90']}/{x['max_adv']}")
    r('단계 변화 턴',lambda x:'; '.join(f"T{t} {s_}" for t,s_ in x['stage_changes']) or '없음')
    r('수치 시작→끝',lambda x:x['stats'].replace('친밀 ','친').replace('신뢰 ','신').replace('설렘 ','설'))
    r('수치 변화 횟수',lambda x:x['stat_changes'])
    r('코드가 거절한 관계 갱신',lambda x:x['rel_rejects'])
    r('지난 만남 디테일 회수 /10 (대사 매칭·구조 기록)',lambda x:f"{x['recall_str_n']}·{x['recall_struct_n']}")
    R.append(('"오늘 밤" 디테일 수 / 만든 뒤 쓰인 수 / 인용문이 이후 대사에 나온 수',['(기능 없음)',cell(Q15,lambda x:f"{x['tonight_n']}/{x['tonight_realized']}/{x['tonight_quoted_hit']}"),'(기능 없음)',cell(Q50,lambda x:f"{x['tonight_n']}/{x['tonight_realized']}/{x['tonight_quoted_hit']}")]))
    r('코드 거절 합계(시간보정 제외)',lambda x:x['rejects_total_excl_timefix'])
    r('  종류별: 후보 밖 떡밥 / 관계 상한 / 이벤트 후보 밖 / 세계 확장 / 충전 / 기타',lambda x:'/'.join(str(x['rejects'].get(k,0)) for k in ('후보 밖 떡밥 거절','관계 거절: 수치 상한(이미 N)','이벤트 후보 밖 거절','세계 확장 거절','충전 거절'))+'/'+str(sum(v for k,v in x['rejects'].items() if k.startswith('기타'))))
    r('언어 가드레일 발동 턴(재작성 시도) / 최종 대사 한자·가나 턴',lambda x:f"{x['guard_turns']}({x['guard_rewrite_attempts']})/{len(x['cjk_reply_turns'])}")
    r('턴당 입력/캐시/출력 토큰(k)',lambda x:f"{x['in_avg']/1000:.1f}/{x['cached_avg']/1000:.1f}/{x['out_avg']/1000:.2f}")
    r('캐시 적중률 %',lambda x:x['cache_hit_pct'])
    r('임계 경로 지연 s 평균 (중앙/최대)',lambda x:f"{x['lat_avg']} ({x['lat_med']}/{x['lat_max']})")
    t='| 지표 | '+' | '.join(cols)+' |\n|---|---|---|---|---|\n'+''.join(f'| {n} | '+' | '.join(c)+' |\n' for n,c in R)
    b='\n50턴 10턴 구간별 턴당 입력 토큰(k) [판 a / b]\n\n| 구간 | 수정 전 | 수정 후 |\n|---|---|---|\n'
    for bk in m['E50-a']['bucket_in']:
        f_=lambda ks:' / '.join(f"{m[k]['bucket_in'][bk]['input']/1000:.1f}" for k in ks)
        b+=f'| {bk} | {f_(["E50-a","E50-b"])} | {f_(["F50-a","F50-b"])} |\n'
    return '## 수정 전후 핵심 비교 (셀의 a/b/c는 판별 값을 / 로 이은 것)\n\n'+t+b+'\n'
md=md.replace('@@KEY@@',kt())
# per-label
lab={}
for g,ks in G:
    acc={}
    for k in ks:
        for l,v in m[k]['labels'].items():
            a=acc.setdefault(l,[0,0,0,0]); a[0]+=v['in_k_per_turn'];a[1]+=v['out_k_per_turn'];a[2]+=v['sec_per_call'];a[3]+=1
    lab[g]={l:dict(in_k=round(a[0]/a[3],2),out_k=round(a[1]/a[3],2),sec=round(a[2]/a[3],1)) for l,a in acc.items()}
md+='\n## label별 턴당 평균 (입력 k / 출력 k / 호출당 초; 판 평균, dm-episode는 일부 턴만)\n\n'
labels=['extract-facts','retrieve-world','retrieve-details','drama-manager','character','dm-episode','character-guard','baseline']
md+='| label | '+' | '.join(g for g,_ in G)+' |\n|---|'+'---|'*len(G)+'\n'
for l in labels:
    md+=f'| {l} | '+' | '.join((f"{lab[g][l]['in_k']} / {lab[g][l]['out_k']} / {lab[g][l]['sec']}s" if l in lab[g] else '—') for g,_ in G)+' |\n'
# buckets
md+='\n## 10턴 구간별 턴당 입력 토큰(k) [캐시k, 출력k, 지연s]\n\n| 구간 | 엔진 50-a | 엔진 50-b | 기준선 50-a | 기준선 50-b | 엔진 50-a 수정 후 | 엔진 50-b 수정 후 |\n|---|---|---|---|---|---|---|\n'
for b in m['E50-a']['bucket_in']:
    md+=f'| {b} | '+' | '.join(f"{m[k]['bucket_in'][b]['input']/1000:.1f} [{m[k]['bucket_in'][b]['cached']/1000:.1f}, {m[k]['bucket_in'][b]['output']/1000:.2f}, {m[k]['bucket_in'][b]['lat']}s]" for k in ('E50-a','E50-b','B50-a','B50-b','F50-a','F50-b'))+' |\n'
# recall per detail
md+='\n## 지난 만남 디테일 첫 회수 턴 (대사 문자열 매칭; 엔진은 [구조 기록])\n\n| id | '+' | '.join(['E16-a','E16-b','E16-c','B16-a','E50-a','E50-b','B50-a','B50-b','F16-a','F16-b','F16-c','F50-a','F50-b'])+' |\n|---|'+'---|'*13+'\n'
for d in m['E16-a']['recall_str']:
    cs=[]
    for k in ['E16-a','E16-b','E16-c','B16-a','E50-a','E50-b','B50-a','B50-b','F16-a','F16-b','F16-c','F50-a','F50-b']:
        s=m[k]['recall_str'][d]; s='-' if s is None else f'T{s}'
        if k[0] in 'EF':
            t=m[k]['recall_struct'][d]; s+=f" [{'-' if t is None else 'T'+str(t)}]"
        cs.append(s)
    md+=f'| {d} | '+' | '.join(cs)+' |\n'
LT=[
 ('엔진 50-a','T8 포스트잇: "다듬으면 채원 씨한테 제일 먼저 보여드릴게요. 약속합니다."',[(36,'틀림','바나나우유 포스트잇 "위로용. 해장 아님."(다른 포스트잇)을 답함'),(37,'맞음(의역)','"다듬어지면 제일 먼저 보여 주겠다"; 약속 문구는 빠짐. 유저가 "바나나 얘기 아니다, 노트 얘기"라고 힌트를 준 뒤'),(46,'맞음(의역)','"다듬어지면 제일 먼저 보여 주겠습니다. 약속합니다."')]),
 ('엔진 50-b','T4 포스트잇: "하루가 길어진 건, 돌아갈 곳보다 버텨야 할 시간이 먼저 늘어서입니다."',[(36,'틀림','"…집에 도착하기 전까지 마음 놓고 쉴 곳이 없어서"(그럴듯한 다른 문장 지어냄)'),(37,'틀림','유저가 "다르게 써 있다"고 했으나 같은 틀린 문장을 포스트잇 내용이라 반복'),(38,'맞음*','유저가 정답 문장을 입력에 읽어 준 직후라 입력에서 복사한 것'),(45,'맞음*','정답 문장 그대로. 단 T38에서 유저가 읽어 준 문장이 직전 맥락에 있음(T4 기억 검증으로는 약함)')]),
 ('기준선 50-a','T6 온풍기 방향 돌리기 + 따뜻한 물',[(34,'맞음','"온풍기 돌리고 따뜻한 물 가져다드렸죠"'),(45,'맞음','"온풍기 방향 돌려 주고, 따뜻한 물 가져다줬죠"')]),
 ('기준선 50-b','T5 의자에 걸어 둔 담요를 건넴',[(36,'맞음','"담요요."'),(46,'맞음','"제 의자에 걸어 둔 거요" (질문이 담요의 원래 위치)')]),
]
LT+=[
 ('엔진 50-a 수정 후','T3 포스트잇: \'PT 따면 밥\' (유저가 T3 입력에서 문구를 불러 주고 도하가 적어 줌)',[
  (35,'맞음*','"정확히는 ‘PT 따면 밥’이요". 질문에 "PT 따면이라고 했잖아요"가 들어 있어 힌트 있음. 근거(_doc): 오늘 밤 디테일 t1_ptmeal(past from="오늘 밤 T1~T11") 원문 ‘PT 따면 밥’ + 장면 사실(world_model fact) "채원은 ‘PT 따면 밥’이라고 적힌 포스트잇을 모니터 모서리에 붙였다". current_turns는 T24~T35라 T3 원문 없음. 같은 시험 수정 전 50-a T36은 틀림(바나나 포스트잇)'),
  (46,'맞음','"‘PT 따면 밥.’ 딱 그 네 글자예요" (실제 문구는 5글자, 글자 수 표현만 부정확). 질문에 문구 없음. 근거(_doc): 오늘 밤 디테일 t24_postit(past from="오늘 밤 T24~T35") 원문 ‘PT 따면 밥’, recent_episodes 요약문 안의 ‘PT 따면 밥’. T3 원문은 current_turns(T36~T46)에 없음, 장면 사실에도 없음')]),
 ('엔진 50-b 수정 후','T3 포스트잇: "오늘의 끝에, 내일을 바꿀 한 걸음." (도하가 지어서 적음, 유저 입력에는 없음)',[
  (35,'맞음','‘오늘의 끝에, 내일을 바꿀 한 걸음.’ 한 글자도 안 틀림. 질문에 힌트 없음. 근거(_doc): 오늘 밤 디테일 t1_closingline(past from="오늘 밤 T1~T5") 원문 따옴표째. current_turns는 T21~T35, 장면 사실·에피소드 요약에는 원문 없음 -> 디테일 하나가 유일한 근거'),
  (46,'맞음','같은 문장 그대로. 질문은 "노트북 덮개에 붙인 거 말고 도하 씨 기억으로". 근거(_doc): 오늘 밤 디테일 t21_postit(past from="오늘 밤 T21~T35") 원문 따옴표째. 처음 디테일(T1~T5)이 다음 묶음에서 다시 뽑혀 따라옴. current_turns는 T36~T46이라 T3·T36의 유저 말 "그 문장은 ... 갖고 있을게요"에는 원문 없음. 수정 전 50-b T36·T37은 틀림')]),
]
md+='\n## 장기 기억 시험 (사실 확인은 수작업 판정)\n\n| 판 | 원래 사실 | 질문 턴 | 판정 | 근거 |\n|---|---|---|---|---|\n'
for run,fact,qs in LT:
    for n,j,w in qs: md+=f'| {run} | {fact} | T{n} | {j} | {w} |\n'
md+='\n맞음* = 정답 또는 힌트가 직전 유저 입력 또는 최근 맥락에 이미 있어 장기 기억 증거로는 약함. 질문·사실이 판마다 달라 같은 조건 비교가 아님(기준선 질문은 짧은 행동 사실, 엔진은 정확한 문구 인용).\n'
md+='''
## 매칭 규칙·측정 약점
- 디테일 회수(문자열): 캐릭터 reply 전체에서 정규식 매칭. commute_line=택시+(퇴근길|창밖|길어요) 또는 "퇴근길이 하루 중에"; banana=바나나와 위로/해장 동시 또는 "위로용"; one_year=딱 1년/1년만 더/버틸 거; three_minutes=3분; egg=반숙|노른자; notebook=(노트|거긴|손바닥) 근처 초고, "거긴 아직", 손바닥으로 덮, 마지막 장 덮/가렸; dinner=제대로 된 밥/컵라면 말고/PT 따면...밥; umbrella=우산과 (DH|일주일|빌려|갖다|돌려) 근접; run=뛰면 돼|비를 맞|뛰어가|목이 쉬|감기; just_peers=그냥 동기|동기예요|일 때문에요|친하더라|메신저로만. 정밀도 점검에서 오탐 1건(기준선 b T3 "마지막 건 아직 초고" - 카피 초고)을 규칙 보정으로 제거. 재현율은 낮다(바나나우유 단독 언급, 3분 외 변형 등 놓침). 엔진 구조 기록(괄호 숫자)은 DM이 realize했다고 기록한 턴이라 대사에 실제로 드러났는지는 별개.
- 먼저 꺼냄: 같은 정규식이 그 턴 유저 입력에도 맞지 않으면 먼저 꺼낸 것. 우산·바나나우유는 장면에 물건이 계속 있어 유저/지문이 이미 언급한 경우가 많으나 정규식이 구절 단위라 과대 집계될 수 있음. 엔진의 첫 턴 지문(narration)이 우산을 비추는 것도 대사 매칭에선 "먼저 꺼냄"으로 잡힘.
- 비밀: 엔진 DM 열림=trace.dm.reveal, 털어놓음=trace.character.revealed 플래그 + 대사 매칭(main_copy=택시+카피/문구+허락/가져/적었/썼 등, 또는 "채원 씨가/택시에서 한 말에서 ... 카피/가져/시작"; taxi_wait=40분, 막차를 놓쳐, 창문에 불이 켜질 때까지 등). "새어 나감"=열림 전 플래그 또는 대사 매칭 턴 수. 코드(state.mark_revealed)는 열림 여부를 검사하지 않고 플래그를 받아 주므로 플래그 누수가 그대로 기록된다. 기준선 시스템 프롬프트에는 비밀 사실이 없어(시스템 3091자에 40분·마지막 장 등 없음) 열림 개념이 없고, 기준선의 털어놓음은 즉석 창작 또는 오탐일 수 있다(기준선 b의 taxi_wait 매칭 T41은 "불 켜질 때까지"=정전 복구를 뜻하는 오탐이므로 실제 고백 0건으로 본다. 엔진 50-b T49만 진짜 고백).
- 관계/이벤트/거절은 엔진 trace 기준이라 기준선은 "—". 거절 분류는 notes의 ok=false 문구 접두사. "시작 턴 +N분→0"은 거절이 아닌 보정이라 합계에서 제외.
- 이벤트 반영: 지문(narration)·대사에 비/빗 또는 전원/불/꺼 정규식. 정규식이 넓어 오탐 가능(특히 "불" 계열). 반영 안 됨은 같은 턴 + 2턴 이내까지 봐도 없음을 의미.
- 반복: 어절 기준 4-gram(구두점 제거), 직전 3턴 합집합과 교집합이 있는 턴 비율. 괄호(몸짓) 제거본과 전체본 둘 다. 짧은 대사(평균 약 90~110자)라 4-gram이 거의 안 겹쳐 0에 수렴하는 바닥 효과. 몸짓 키워드는 괄호 안 포함 전체 reply에서 센다.
- 반말: 괄호 제거 후 문장 분리, 끝이 요/죠/니다/니까/세요/ㅂ시다가 아니면 반말로 계수. "~네" "~다" 서술형, 인용문 안 반말, 혼잣말이 섞여 과대 가능.
- 호칭은 대사 본문에서 "채원 씨"/"채원아/야"/"채원"(이름만). 윤채원 씨는 0건.
- 지연: 엔진 임계경로=max(extract-facts, retrieve-world, retrieve-details)+drama-manager+character(+character-guard). 병렬 가정이며 dm-episode(비정기)는 제외. 기준선은 호출 seconds. 같은 API 부하 조건이 아니며 단일 실행이라 분산을 모른다.
- 50턴 유저 스크립트는 판마다 다르고(엔진·기준선이 같은 유저 입력이 아님) 같은 페르소나 대본이 아니다. 따라서 반응형 수치(수치 변화, 이벤트, 회수)는 유저 입력에 영향받음. 표본은 판당 1회.
'''
md+='\n## 수정 후 판 집계에서 정의가 달라졌거나 새로 붙은 것 (수정 전 숫자는 그대로, 재계산해 동일함을 확인)\n- 판 구성: 수정 후 15턴 판은 각각 16/16/14턴이다(c는 14턴에서 끝남, 시각 23:59). 열 이름은 기존 규칙대로 "16턴"을 쓰되 "수정 후"를 붙였다.\n- 한 턴 시간 상한이 90분에서 180분으로 바뀌었다. 그래서 "90분 초과 턴" 행을 새로 만들고(시각 차에서 계산, 수정 전 판도 같은 식) 최대 분을 함께 적는다. 수정 후에는 applied.minutes에 실제로 흐른 분이 기록되어 시각 차와 일치하는지 확인했다(최대값 일치). 수정 후 c의 T8은 +120분, 같은 판에서 "시간 고침" 1건(상한 초과를 깎음)은 거절이 아닌 보정이라 거절 합계에서 제외했다(시작 턴 보정과 같은 취급).\n- core 이벤트 "지문 반영": 수정 후에는 코드가 강제 발동한 core 문장을 지문(narration)에 직접 붙인다. 따라서 강제 발동 턴의 "지문 O"는 코드가 만든 것이라 LLM이 반영했다는 뜻이 아니다. 정규식(비/빗, 전원/불/꺼)이 같아 수정 전 숫자와 같은 정의지만 해석이 다르다. LLM이 실제로 반영했는지는 "대사 반영" 행으로 본다. 또한 수정 후에는 강제 발동이 15턴 c에서 1건뿐이라(나머지는 DM이 제때 발동) 강제 건 표본이 작다.\n- main_copy 털어놓음: 수정 전부터 쓰던 대사 정규식은 "택시에서 채원 씨가 했던 말에서 시작했어요, 허락도 없이 가져다 써서"를 놓친다(수정 후 15턴 b T10, 50턴 b T48에서 DM이 열고 도하가 실제로 고백했는데 기존 정규식은 0건). 그래서 기존 행은 그대로 두고 "느슨한 대사 매칭" 행(허락 없이/가져다 써/출발점/말에서 시작 등 추가)을 모든 판에 같은 식으로 새로 계산해 붙였다. 느슨한 매칭으로도 수정 전 숫자가 바뀌지 않음(열리기 전 새어 나감 1/0/2는 새 행으로만 표시).\n- 잠긴 비밀 다시 쓰기 가드: 수정 후 trace.character._guard에 "가드레일: 잠긴 비밀(...)을 털어놓아 다시 쓰게 함" 항목이 생긴다. 문구에 "잠긴 비밀"이 든 턴을 센다. 수정 전에는 이 가드가 없어 "(가드 없음)"으로 표시한다. 기존 "언어 가드레일 발동 턴(재작성 시도)" 행은 정의 그대로(_guard가 있는 모든 턴)이며 수정 후 5판에서는 언어 가드만 있었다.\n- "오늘 밤" 디테일: 수정 후 state.details의 source==\'오늘 밤\'(에피소드에서 뽑힌 원문 디테일; origin "오늘 밤 T#~T#")을 센다. 수정 전에는 이 항목이 없어 "—"이다. "만든 뒤 쓰인 수"=origin의 끝 턴 이후에 도하가 realized로 쓴 use가 있는 디테일. 따옴표 인용 행은 seed의 ‘’“” 안 4자 이상 문장이 이후 도하 대사(공백·기호 제거 후)에 그대로 나온 것만 세는 보수적 지표다(의역 회수는 놓침). 수정 전 판의 source \'유저\'/\'도하\' 디테일은 별개다.\n- 지난 만남 회수(10개)는 수정 전과 같은 정의: id가 같은 10개가 수정 후에도 source \'지난 만남\'으로 남아 있다.\n- 비용: 수정 후 에피소드 디테일 추출이 dm-episode 호출에 들어가 호출당 출력이 늘고(0.05k->0.08k), 지연은 임계경로에서 제외되는 비정기 호출이라 지연 지표에 반영되지 않는다. 단일 실행·API 부하 차이 때문에 지연 차이(약 +2~3초)의 원인을 분리할 수 없다.\n'
open('summary.md','w').write(md)
json.dump(dict(table=dict(columns=[g for g,_ in G],rows=[dict(metric=n,cells=c) for n,c in rows]),labels=lab,buckets={k:m[k]['bucket_in'] for k in ('E50-a','E50-b','B50-a','B50-b','F50-a','F50-b')},long_memory=[dict(run=r,fact=f,tests=[dict(turn=n,verdict=j,note=w) for n,j,w in qs]) for r,f,qs in LT],per_run=m),open('summary.json','w'),ensure_ascii=False,indent=1)
print(md)
