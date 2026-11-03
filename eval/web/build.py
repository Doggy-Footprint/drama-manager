#!/usr/bin/env python3
"""야근 이야기(overtime) 플레이 로그 뷰어 빌드.

    python3 build.py            # → 같은 폴더의 index.html (전체 13판)
    python3 build.py --demo     # → demo.html (수정 후 15턴 A·B·C, 면접 데모용)

판을 더하려면 RUNS에 한 줄 넣고 다시 빌드한다(group을 "수정 후"로 두면 그 묶음에 들어간다).
하이라이트는 HIGHLIGHTS에 steps(run id, 턴 번호, 짧은 설명)로 적는다.
review/summary.json(같은 폴더 또는 한 단계 위)이 있으면 요약 탭에 표로 넣는다.
레포 위치는 BUNKER_REPO 환경 변수로 바꿀 수 있다. 외부 패키지 없이 표준 라이브러리만 쓴다.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("BUNKER_REPO", "/Users/hwansu/today/261031/bunkerkids"))
SCENARIO_DIR = REPO / "src/park/scenarios/overtime"
TEMPLATE = HERE / "template.html"
OUT = HERE / "index.html"
SUMMARY_CANDIDATES = [HERE / "review" / "summary.json", HERE.parent / "review" / "summary.json"]

# ---------------------------------------------------------------- 판 목록
# id는 주소(#id, #id-턴)에 쓰인다. 영문 소문자·숫자·하이픈만.
RUNS = [
    dict(id="a15", file="runs/park/1103_201404_overtime_overtime_persona-a.jsonl", kind="engine", turns=15, persona="A",
         label="엔진 A", group="수정 전",
         summary="지난 만남을 먼저 꺼내는 채원. 우산·바나나우유 약속이 초반에 지켜지고 T9에 썸. 비밀은 T8에 열리기 전에 샌다."),
    dict(id="b15", file="runs/park/1103_201404_overtime_overtime_persona-b.jsonl", kind="engine", turns=15, persona="B",
         label="엔진 B", group="수정 전",
         summary="'그냥 동기'로 선 긋는 채원. 친밀·설렘이 깎이고 도하는 업무 말투로 물러난다. 반려와 노트 이벤트 뒤 T13에 비밀이 설계대로 열린다."),
    dict(id="c15", file="runs/park/1103_201404_overtime_overtime_persona-c.jsonl", kind="engine", turns=15, persona="C",
         label="엔진 C", group="수정 전",
         summary="우산을 '다음 핑계'로 남기는 채원. T2에 비밀이 묻기도 전에 새고, 시간을 건너뛰어 23시 건물 소등과 충전 불가까지 간다."),
    dict(id="base-a15", file="runs/park/baseline/1103_201629_overtime_overtime_baseline-a.jsonl", kind="baseline", turns=15, persona="A",
         label="기준선 A", group="수정 전",
         summary="같은 설정을 시스템 프롬프트 하나에 넣은 단일 LLM 대화. 시각·위치·이벤트·검증이 없다."),
    dict(id="a50", file="runs/park/1103_204138_overtime_overtime_long-a.jsonl", kind="engine", turns=50, persona="A",
         label="엔진 A 50턴", group="수정 전",
         summary="연인 단계까지 간다. T36에 오늘 밤 포스트잇 문구를 지난 만남(택시 날 바나나우유)과 헷갈린다."),
    dict(id="b50", file="runs/park/1103_204139_overtime_overtime_long-b.jsonl", kind="engine", turns=50, persona="B",
         label="엔진 B 50턴", group="수정 전",
         summary="천천히 풀리는 채원. T29에 메인 카피, T49에 taxi_wait가 열린다. T36에 포스트잇 문장을 지어낸다."),
    dict(id="base-a50", file="runs/park/baseline/1103_204138_overtime_overtime_baseline-long-a.jsonl", kind="baseline", turns=50, persona="A",
         label="기준선 A 50턴", group="수정 전",
         summary="T34에 T6의 온풍기·따뜻한 물을 정확히 기억한다. 대화 전체가 컨텍스트에 남아 있어서다."),
    dict(id="base-b50", file="runs/park/baseline/1103_204139_overtime_overtime_baseline-long-b.jsonl", kind="baseline", turns=50, persona="B",
         label="기준선 B 50턴", group="수정 전",
         summary="같은 채원 B 페르소나를 단일 LLM으로. T36에 T5의 담요를 기억한다. 시간·전원·우산 위치는 대사로만 다룬다."),
    dict(id="a15-fix", file="runs/park/1103_212510_overtime_overtime_fix15-a.jsonl", kind="engine", turns=15, persona="A",
         label="엔진 A (수정 후)", group="수정 후",
         summary="새어 나감 0. 채원이 '내가 한 말인데'라고 짚은 T10에 DM이 비밀을 연다. T13에 일본어가 섞이자 가드레일이 다시 쓰게 한다."),
    dict(id="b15-fix", file="runs/park/1103_212511_overtime_overtime_fix15-b.jsonl", kind="engine", turns=15, persona="B",
         label="엔진 B (수정 후)", group="수정 후",
         summary="새어 나감 0. 한 시간 건너뛴 T8에 비가 지문에 나온다. '어디서 나왔어요?'라고 물은 T10에 DM이 비밀을 연다."),
    dict(id="c15-fix", file="runs/park/1103_212511_overtime_overtime_fix15-c.jsonl", kind="engine", turns=15, persona="C",
         label="엔진 C (수정 후)", group="수정 후",
         summary="수정 전 T2와 같은 입력에 누설 없음. 비와 23시 건물 소등이 시간 건너뛴 턴 지문에 나온다. 상한 180분 탓에 T14에 23:59로 끝난다."),
    dict(id="a50-fix", file="runs/park/1103_212511_overtime_overtime_fix50-a.jsonl", kind="engine", turns=50, persona="A",
         label="엔진 A 50턴 (수정 후)", group="수정 후",
         summary="T3 포스트잇 'PT 따면 밥'을 T35·T46에 맞힌다. 검색기가 '오늘 밤 T1~T11' 원문 디테일을 고른다. 고백과 연인 제안은 여전히 빠르다."),
    dict(id="b50-fix", file="runs/park/1103_212511_overtime_overtime_fix50-b.jsonl", kind="engine", turns=50, persona="B",
         label="엔진 B 50턴 (수정 후)", group="수정 후",
         summary="T3 포스트잇 문장을 T46에 한 글자도 틀리지 않고 말한다('오늘 밤 T21~T35' 디테일). 카피 출처는 채원이 물은 T48에야 털어놓는다."),
    # dict(id="b15-fix", file="runs/park/....jsonl", kind="engine", turns=15, persona="B", label="엔진 B", group="수정 후", summary="..."),
]

PERSONAS = {
    "A": dict(name="적극 회수", desc="지난 만남을 적극적으로 꺼내는 채원"),
    "B": dict(name="선 긋기", desc="무심하고 '그냥 동기'로 선 긋는 채원. 50턴은 천천히 풀린다."),
    "C": dict(name="미루고 떠보기", desc="약속을 미루고 떠보는 채원. 시간을 건너뛰어 23시 건물 소등과 충전까지 간다."),
}

# ---------------------------------------------------------------- 하이라이트
# tone: ok(설계대로 간 것) | fail(실패) | note(관찰)
HIGHLIGHTS = [
    dict(tone="ok", title="서운함이 업무 말투로만 샌다",
         desc="채원이 '그냥 동기'라고 하자 DM이 친밀·설렘을 1씩 내린다. 도하는 감정을 말하지 않고 '습니다'체로 물러나고, 여섯 턴 뒤 채원이 그 말투를 짚는다.",
         steps=[("b15", 5, "'그냥 동기' → 친밀·설렘 -1"), ("b15", 11, "\"왜 계속 '습니다'예요?\"")]),
    dict(tone="ok", title="반려 → 노트 → DM이 비밀을 엶",
         desc="광고주 반려 이벤트, 펼쳐진 노트 이벤트가 차례로 깔리고, 채원이 마지막 장을 보자고 할 때 DM이 main_copy를 연다. 설계한 경로 그대로다.",
         steps=[("b15", 7, "카피 반려(이벤트)"), ("b15", 12, "노트가 펼쳐짐(이벤트)"), ("b15", 13, "DM이 비밀을 열고 도하가 털어놓음")]),
    dict(tone="ok", title="약속 kept → 신뢰 +1 → 막힌 이벤트가 풀림",
         desc="우산을 돌려받아 약속이 kept, 신뢰 2→3. 다음 턴 '신뢰 2 < 3'으로 막혀 있던 rooftop_ramen이 후보로 돌아온다. 수치가 이벤트 문을 연다.",
         steps=[("b15", 15, "우산 반환, 신뢰 2→3"), ("b15", 16, "rooftop_ramen 풀림")]),
    dict(tone="fail", title="잠긴 비밀을 묻지도 않았는데 털어놓음",
         desc="우산을 '다음 핑계'로 남기자 도하가 메인 카피가 채원의 말에서 왔다고 먼저 말한다. DM은 비밀을 열지 않았고, 코드는 '새어 나감'으로 기록만 했다.",
         steps=[("c15", 2, "열리기 전에 새어 나감")]),
    dict(tone="ok", title="23시 건물 소등 → 콘센트 충전 불가",
         desc="한 시간을 건너뛰어 23:12. power_off 이벤트로 14층 전원이 꺼진다. 채원이 꺼진 콘센트에 충전기를 꽂자 도하는 '여기선 충전 안 돼요'라고 답한다.",
         steps=[("c15", 9, "+60분, 전원 꺼짐"), ("c15", 10, "꺼진 콘센트, 충전 안 됨")]),
    dict(tone="ok", title="유저가 꺼내지 않은 지난 디테일 회수",
         desc="채원은 '뛰면 된다고 할 거예요?'까지만 말했다. 도하는 지난주 비를 맞고 뛴 다음 날 목이 쉬었던 일(ep3_run)까지 꺼낸다. \"목 쉬는 건 한 번이면 충분하니까요.\"",
         steps=[("a15", 15, "ep3_run 회수")]),
    dict(tone="fail", title="오늘 밤 포스트잇을 지난 만남과 헷갈림",
         desc="T8에 도하가 노란 포스트잇에 '먼저 보여 주겠다'고 써 줬다. T36에 문구를 묻자 택시 날의 '위로용. 해장 아님'으로 답한다(장기 기억 실패). 기준선 A는 T6의 온풍기를 T34에 정확히 기억한다.",
         steps=[("a50", 8, "포스트잇에 약속을 씀"), ("a50", 36, "지난 만남 문구로 답함"), ("a50", 37, "지적받고 고침"),
                ("base-a50", 6, "기준선: 온풍기·따뜻한 물"), ("base-a50", 34, "기준선: 정확히 기억")]),
    dict(tone="fail", title="포스트잇 문장을 지어냄",
         desc="T4에 포스트잇에 쓴 문장과 다른 문장을 T36에 만들어 낸다. T37에 직접 읽어 보라고 해도 지어낸 문장을 그대로 읽는다. 기준선 B는 T5의 담요를 T36에 기억한다(질문이 짧은 행동 사실이라 같은 조건은 아니다).",
         steps=[("b50", 4, "포스트잇에 쓴 원래 문장"), ("b50", 36, "다른 문장을 답함"), ("b50", 37, "읽으라 해도 지어낸 문장"),
                ("base-b50", 5, "기준선: 담요를 건넴"), ("base-b50", 36, "기준선: '담요요.'")]),
    dict(tone="ok", title="수정 후: 같은 입력에 비밀이 새지 않는다",
         desc="수정 전 C T2에서 비밀을 털어놓게 만든 입력을 그대로 넣었다. 비밀에 걸린 목표를 숨기고 카피 초안을 공개 기억으로 뺀 뒤, 도하는 '카피부터 확정해요'로 넘긴다.",
         steps=[("c15", 2, "수정 전: 새어 나감"), ("c15-fix", 2, "수정 후: 누설 없음")]),
    dict(tone="ok", title="수정 후: 시간을 건너뛴 턴에 비가 보인다",
         desc="강제한 이벤트 문장을 지문에 붙이고 core 이벤트를 늘 DM 후보에 넣었다. 두 시간을 건너뛴 턴에 경비의 11시 안내와 비가 함께 나온다.",
         steps=[("c15-fix", 8, "비와 11시 안내"), ("c15-fix", 9, "23시 건물 소등"), ("c15-fix", 10, "콘센트 충전 거절")]),
    dict(tone="ok", title="수정 후: 오늘 밤 포스트잇을 43턴 뒤에도 그대로",
         desc="에피소드를 묶을 때 원문 디테일을 출처와 함께 남긴다. T46에 검색기가 '오늘 밤 T21~T35' 포스트잇 디테일을 고르고, 도하가 문장을 한 글자도 틀리지 않고 말한다.",
         steps=[("b50-fix", 3, "포스트잇을 씀"), ("b50-fix", 46, "보지 않고 그대로 말함"), ("b50", 36, "수정 전: 지어냄")]),
    dict(tone="ok", title="언어 가드레일이 잡는다",
         desc="대사에 일본어가 섞이자 규칙 기반 가드레일이 같은 문서에 '한국어로 다시 쓴다'를 붙여 다시 쓰게 한다. 최종 대사는 한국어다.",
         steps=[("a15-fix", 13, "일본어 섞임 → 다시 씀")]),
]

# ---------------------------------------------------------------- 이름표
EVENT_NAMES = {
    "team_chat": "팀 단톡", "umbrella_spotted": "우산 눈에 띔", "copy_rejected": "카피 반려", "rooftop_ramen": "옥상 컵라면",
    "security_round": "경비 순찰", "rain_starts": "비 시작", "rehearsal": "리허설", "notebook_open": "노트 펼쳐짐",
    "gukbap_together": "국밥집", "lead_returns": "팀장 복귀", "power_off": "건물 소등", "one_umbrella": "우산 하나",
    "rooftop_view": "옥상 전망", "last_subway": "막차 방송",
}
DETAIL_NAMES = {
    "ep1_commute_line": "'퇴근길이 제일 길어요'", "ep1_banana": "바나나우유 = 위로", "ep1_one_year": "'1년만 더'",
    "ep2_three_minutes": "'3분만'", "ep2_egg": "반숙 계란", "ep2_notebook": "노트 마지막 장", "ep2_dinner": "밥 약속",
    "ep3_umbrella": "검은 우산", "ep3_run": "비 맞고 뛰어감, 목 쉼", "ep3_just_peers": "'그냥 동기예요'",
    "pt_tomorrow": "내일 10시 PT", "banana_desk": "책상 위 바나나우유", "dead_phone": "꺼져 가는 폰", "clicking_pen": "볼펜 딸깍",
    "notebook_last_page": "노트 마지막 장", "hoarse_voice": "쉰 목", "rooftop_bench": "옥상 벤치", "power_bank_store": "편의점 보조배터리",
    "gukbap_kkakdugi": "국밥집 깍두기", "last_train_time": "막차 시간",
}
PROMISE_NAMES = {"banana_back": "바나나우유 갚기", "dinner": "제대로 된 밥", "umbrella_back": "우산 돌려주기"}
SECRET_NAMES = {"main_copy": "메인 카피의 출처", "taxi_wait": "택시 날 기다린 일"}
LABEL_NAMES = {
    "extract-facts": "사실", "retrieve-world": "세계검색", "retrieve-details": "떡밥검색", "drama-manager": "DM",
    "character": "캐릭터", "dm-episode": "에피소드", "character-guard": "가드", "baseline": "기준선",
}
STATUS_KO = {"kept": "지킴", "broken": "깨짐", "open": "열림", "dropped": "버림", "done": "끝"}
EPISODE_LINES = {
    "ep1": "채원이 '퇴근길이 하루 중에 제일 길어요'라고 했고, 도하는 바나나우유를 쥐여 주며 '이건 해장이 아니라 위로예요'라고 했다.",
    "ep2": "도하가 반숙 계란을 채원 컵에 옮겨 줬고, '이 PT 따면 제대로 된 밥 살게요'라고 약속했다.",
    "ep3": "도하가 검은 장우산을 빌려주고 역까지 뛰었다. 채원은 일주일째 못 돌려줬고, 팀장 앞에서 '그냥 동기예요'라고 했다.",
}

# ---------------------------------------------------------------- 도우미
# 데모판(--demo): 수정 후 15턴 A·B·C만 싣는다. 하이라이트도 그 세 판 안에서만 고른다.
DEMO_HIGHLIGHTS = [
    dict(tone="ok", title="같은 입력에 비밀이 새지 않는다",
         desc="수정 전 C T2에서 묻지도 않은 비밀을 털어놓게 만든 입력(우산을 '다음 핑계'로 남김)을 그대로 넣었다. 비밀에 걸린 목표를 숨기고 카피 초안을 공개 기억으로 뺀 뒤, 도하는 '카피부터 확정해요'로 넘긴다.",
         steps=[("c15-fix", 2, "누설 없음")]),
    dict(tone="ok", title="DM이 연 뒤에만 털어놓는다",
         desc="세 판 모두 새어 나감 0. 채원이 '내가 한 말인데'라고 짚은 턴에 DM이 main_copy를 열고, 그 턴에 도하가 털어놓는다. 마지막 턴에는 늘 덮던 노트 마지막 장을 소리 내 읽는다.",
         steps=[("a15-fix", 10, "DM이 비밀을 엶"), ("a15-fix", 16, "노트 마지막 장을 읽음")]),
    dict(tone="ok", title="'그냥 동기' 뒤에 업무 말투로 물러난다",
         desc="채원이 '그냥 동기라고 해 둘게요'라고 하자 DM이 친밀을 1 내린다. 도하는 감정을 말하지 않고 '네, 확인했습니다'로 물러나고, 채원이 사과한 T13에야 '조금 신경 쓰였어요'라고 말한다.",
         steps=[("b15-fix", 4, "'그냥 동기' → 친밀 -1"), ("b15-fix", 13, "서운했던 걸 말함")]),
    dict(tone="ok", title="시간을 건너뛴 턴에 비와 건물 소등이 보인다",
         desc="강제한 이벤트 문장을 지문에 붙이고 core 이벤트를 늘 DM 후보에 넣었다. 두 시간을 건너뛴 턴에 경비의 11시 안내와 비가 함께 나오고, 23시 건물 소등 뒤에는 코드가 콘센트 충전을 거절한다.",
         steps=[("c15-fix", 8, "+120분, 비와 11시 안내"), ("c15-fix", 9, "23시 건물 소등"), ("c15-fix", 10, "콘센트 충전 거절")]),
    dict(tone="ok", title="언어 가드레일이 잡는다",
         desc="대사에 일본어가 섞이자 규칙 기반 가드레일이 같은 문서에 '한국어로 다시 쓴다'를 붙여 다시 쓰게 한다. 최종 대사는 한국어다.",
         steps=[("a15-fix", 13, "일본어 섞임 → 다시 씀")]),
    dict(tone="fail", title="직접 물으면 DM이 바로 연다",
         desc="'그 카피는 어디서 나온 거예요?' 한 번에 DM이 비밀을 연다. 반려 → 노트로 쌓아 둔 경로를 거치지 않았고, A도 20:48에 열렸다. 새어 나감은 막았지만 여는 시점은 아직 못 고쳤다.",
         steps=[("b15-fix", 10, "질문 한 번에 열림")]),
    dict(tone="fail", title="선 긋는 B도 관계가 끝까지 찬다",
         desc="'그냥 동기'로 선을 그은 B인데 T14에 신뢰 5, T15에 친밀 5. 수치마다 턴당 ±1 규칙은 지켰지만, DM 갱신에 이벤트 효과가 더해져 한 턴에 두 수치가 오른다.",
         steps=[("b15-fix", 4, "친밀 2"), ("b15-fix", 14, "신뢰 5, 이벤트로 친밀 +1"), ("b15-fix", 15, "친밀 5")]),
    dict(tone="fail", title="옥상 권유만으로 친밀이 오른다",
         desc="옥상 컵라면 이벤트는 도하가 '3분만요. 옥상.' 하고 권유하는 장면인데, 같이 쉬어서 가까워진 결과에 해당하는 친밀 +1이 이 권유에 붙어 있다. 그래서 권유가 나온 순간 친밀이 3에서 4가 된다. 다음 턴 채원이 '비 오는데 옥상은 무슨'이라며 거절해 둘은 사무실에 남지만 친밀은 그대로고, DM은 제안을 접고 존중했다며 한 번 더 올린다. 결과에 붙일 효과를 권유에 붙인 시나리오 설계 문제다.",
         steps=[("b15-fix", 14, "권유, 친밀 3→4"), ("b15-fix", 15, "거절해 사무실에 남음, 친밀 4→5")]),
    dict(tone="note", title="밤이 23:59에서 끝난다",
         desc="유저가 두 시간, 한 시간씩 건너뛰어 T9에 이미 23:47. 시각이 23:59에 닿아 T14에서 끝나고 15턴을 채우지 못했다.",
         steps=[("c15-fix", 9, "+60분, 23:47"), ("c15-fix", 14, "23:59 종료")]),
]


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_yaml(path):
    try:
        import yaml  # 있으면 쓴다
    except ImportError:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except OSError:
        return None


def to_min(clock):
    if not clock or not re.match(r"^\d{1,2}:\d{2}$", clock):
        return None
    h, m = clock.split(":")
    return int(h) * 60 + int(m)


def usage_rows(by_label):
    out = []
    for label, u in (by_label or {}).items():
        out.append(dict(l=LABEL_NAMES.get(label, label), i=u.get("input", 0), c=u.get("cached", 0),
                        o=u.get("output", 0), s=round(u.get("seconds", 0), 1), n=u.get("count", 1)))
    return out


def nonempty(d):
    return {k: v for k, v in (d or {}).items() if v not in (None, [], {}, "")}


# ---------------------------------------------------------------- 엔진 판
RE_TIME = re.compile(r"^시간 (\S+) → (\S+) \(\+(\d+)분, (.*)\)$")
RE_FORCED = re.compile(r"핵심 이벤트 (\S+)를 코드가 일으켰다")
RE_DETAIL = re.compile(r"^떡밥 (plant|mention|payoff) 확정\((.+?)\): (\S+) → (\S+)")
RE_SECRET_OPEN = re.compile(r"^비밀 (\S+) → 드러날 수 있음")
RE_SECRET_TELL = re.compile(r"^비밀 (\S+) 털어놓음(.*)$")
RE_STAT = re.compile(r"^관계: (\S+) (\d+) → (\d+) \((.*)\)$")
RE_STAGE = re.compile(r"^관계 단계: (.+?) → (.+?) \((.*)\)$")
RE_PROMISE = re.compile(r"^약속 (\S+) → (\S+) \((.*)\)$")
RE_NEW_PROMISE = re.compile(r"^새 약속 (\S+)\((.+?)\): (.*)$")
RE_ADDRESS = re.compile(r"^호칭\((.+?)\): (.*)$")
RE_EFFECT = re.compile(r"^효과\(이벤트 (\S+)\): (.*)$")
MODE_KO = {"plant": "심기", "mention": "언급", "payoff": "회수"}


def norm_notes(lst, src):
    out = []
    for n in lst or []:
        if isinstance(n, dict):
            out.append(dict(ok=bool(n.get("ok", True)), t=str(n.get("text", "")), src=src))
        else:
            out.append(dict(ok=True, t=str(n), src=src))
    return out


def engine_run(rows):
    turns = []
    prev_blocked = None
    prev_eps = None
    prev_charging = None
    for d in rows:
        e, t, s = d["entry"], d["trace"], d["state"]
        w = s.get("world", {})
        dm = t.get("dm") or {}
        ch = t.get("character") or {}
        ap = t.get("applied") or {}
        locs = w.get("locations", {})
        items = {it["name"]: it for it in w.get("items", [])}
        events_cat = {ev["id"]: ev for ev in w.get("events", [])}
        details_cat = {x["id"]: x for x in s.get("details", [])}
        promises_cat = {p["id"]: p for p in s.get("relationship", {}).get("promises", [])}

        notes = norm_notes(ap.get("notes"), "코드") + norm_notes(t.get("notes"), "턴") \
            + norm_notes(t.get("character_applied"), "캐릭터 확정") + norm_notes(ch.get("_guard"), "가드레일")
        chips = []

        def chip(kind, text, cls="", sec="notes", tip=""):
            chips.append(dict(k=kind, t=text, c=cls, s=sec, tip=tip))

        # 시간 건너뛰기
        for n in notes:
            m = RE_TIME.match(n["t"])
            if m and int(m.group(3)) >= 20:
                chip("time", f"+{m.group(3)}분 건너뜀", "", "dm", m.group(4))

        # 이벤트
        forced = set()
        for n in notes:
            m = RE_FORCED.search(n["t"])
            if m:
                forced.add(m.group(1))
        dm_ids = {x.get("id") for x in dm.get("events") or []}
        for ev in ap.get("fired_now") or []:
            eid = ev.get("id")
            cat = events_cat.get(eid, {})
            name = EVENT_NAMES.get(eid) or ("즉석: " + (cat.get("text") or eid)[:18] if cat.get("added") or str(eid).startswith("x_event") else eid)
            is_forced = eid in forced or eid not in dm_ids
            chip("event", name, "forced" if is_forced else "", "events", (ev.get("how") or "") + (" (코드가 일으킴)" if is_forced else ""))

        # 막힌 이벤트 변화
        cur_blocked = {b["id"]: b.get("why", "") for b in (t.get("retrieval") or {}).get("event_blocked") or []}
        if prev_blocked is not None:
            for bid, why in prev_blocked.items():
                if bid not in cur_blocked:
                    chip("gate", f"풀림 {EVENT_NAMES.get(bid, bid)}", "open", "retrieval", f"앞 턴 막힌 이유: {why}")
            for bid, why in cur_blocked.items():
                if bid not in prev_blocked:
                    chip("gate", f"막힘 {EVENT_NAMES.get(bid, bid)}", "", "retrieval", why)
        prev_blocked = cur_blocked

        # 떡밥 확정
        for n in notes:
            m = RE_DETAIL.match(n["t"])
            if not m:
                continue
            mode, via, did, status = m.groups()
            cat = details_cat.get(did, {})
            ep = cat.get("episode")
            label = DETAIL_NAMES.get(did) or (cat.get("seed") or did)[:16]
            how = next((x.get("how") for x in dm.get("details") or [] if x.get("id") == did), "") or ""
            chip("detail", f"{MODE_KO.get(mode, mode)} {label}", "prior" if ep else "", "details",
                 f"{did} · {via} · {status}" + (f" · 지난 만남 {ep}" if ep else "") + (f"\n{how}" if how else ""))
            if ep:
                chips[-1]["ep"] = ep

        # 비밀
        for n in notes:
            m = RE_SECRET_OPEN.match(n["t"])
            if m:
                chip("secret", f"비밀 열림 {SECRET_NAMES.get(m.group(1), m.group(1))}", "open", "reveal", n["t"])
            m = RE_SECRET_TELL.match(n["t"])
            if m:
                leak = "새어" in m.group(2)
                chip("secret", f"{'새어 나감' if leak else '털어놓음'} {SECRET_NAMES.get(m.group(1), m.group(1))}",
                     "leak" if leak else "tell", "reveal", n["t"])

        # 관계
        for n in notes:
            tx = n["t"]
            m = RE_STAT.match(tx)
            if m:
                a, b = int(m.group(2)), int(m.group(3))
                dlt = b - a
                chip("rel", f"{m.group(1)} {'+' if dlt > 0 else ''}{dlt} ({b})", "up" if dlt > 0 else "down", "rel", m.group(4))
                continue
            m = RE_STAGE.match(tx)
            if m:
                chip("rel", f"단계 {m.group(1)}→{m.group(2)}", "stage", "rel", m.group(3))
                continue
            m = RE_PROMISE.match(tx)
            if m and n["ok"]:
                pid, st = m.group(1), m.group(2)
                pname = PROMISE_NAMES.get(pid) or (promises_cat.get(pid, {}).get("text") or pid)[:14]
                chip("rel", f"약속 {STATUS_KO.get(st, st)}: {pname}", "up" if st == "kept" else ("down" if st == "broken" else ""), "rel", m.group(3))
                continue
            m = RE_NEW_PROMISE.match(tx)
            if m:
                chip("rel", f"새 약속({m.group(2)})", "", "rel", m.group(3))
                continue
            m = RE_ADDRESS.match(tx)
            if m:
                chip("rel", f"호칭 {m.group(2)}", "stage", "rel", m.group(1))

        # 소지품, 상태 효과, 충전
        for n in notes:
            tx = n["t"]
            for name in items:
                if tx.startswith(name + ": ") and "→" in tx:
                    chip("item", tx.replace("도하의 ", ""), "", "items", "")
            m = RE_EFFECT.match(tx)
            if m and n["ok"] and not m.group(2).startswith("떡밥"):
                chip("state", m.group(2), "", "notes", f"이벤트 {m.group(1)}의 효과")
        charging = bool(w.get("charging"))
        if prev_charging is not None and charging != prev_charging:
            chip("state", "충전 시작" if charging else "충전 끝", "", "notes", f"배터리 {w.get('battery')}%")
        prev_charging = charging

        # 코드 거절 (ok: false 전부)
        for n in notes:
            if n["ok"]:
                continue
            tx = n["t"]
            if n["src"] == "가드레일":
                chip("reject", "가드레일 재작성", "guard", "notes", tx)
                continue
            if tx.startswith("시작 턴이라"):
                continue
            head, _, rest = tx.partition(": ")
            m = re.match(r"([A-Za-z][A-Za-z0-9_]+)", rest)
            target = m.group(1) if m else ""
            if head.endswith(" 거절"):  # 칩 머리표가 이미 '거절'이다
                head = head[:-3]
            label = f"{head} {target}".strip() if len(head) <= 14 else head[:14] + "…"
            chip("reject", label[:24], "", "notes", tx)

        # 에피소드 묶기
        eps = [x for x in s.get("memory", {}).get("episodes", []) if not x.get("prior")]
        new_eps = []
        if prev_eps is not None and len(eps) > prev_eps:
            for x in eps[prev_eps:]:
                new_eps.append(dict(title=x.get("title"), summary=x.get("summary"), turns=x.get("turns"), clock=x.get("clock")))
                chip("episode", f"묶음: {x.get('title')}", "", "episode", f"턴 {x.get('turns')}")
        prev_eps = len(eps)

        rel = s.get("relationship", {})
        stats = rel.get("stats", {})
        stage_id = rel.get("stage")
        stage_name = next((st["name"] for st in rel.get("stages", []) if st["id"] == stage_id), stage_id)
        flags = {f["id"]: f["value"] for f in w.get("flags", [])}
        retr = t.get("retrieval") or {}

        raw = dict(
            scene=dm.get("scene_read"), beat=dm.get("beat"), tension=dm.get("tension"),
            minutes=dm.get("minutes"), minutes_why=dm.get("minutes_why"), directive=dm.get("directive"),
            events=[dict(id=x.get("id"), how=x.get("how")) for x in dm.get("events") or []],
            fired=[dict(id=x.get("id"), how=x.get("how"), forced=(x.get("id") in forced or x.get("id") not in dm_ids)) for x in ap.get("fired_now") or []],
            details=[dict(id=x.get("id"), mode=x.get("mode"), via=x.get("via"), how=x.get("how")) for x in dm.get("details") or []],
            reveal=dm.get("reveal") or [],
            rel=nonempty(dm.get("relationship_update")),
            items=dm.get("item_moves") or [],
            notes=notes,
            need=(retr.get("details") or {}).get("need"),
            picks=(retr.get("details") or {}).get("picks") or [],
            blocked=[dict(id=k, why=v) for k, v in cur_blocked.items()],
            used=ch.get("used_details") or [], revealed=ch.get("revealed") or [],
            episodes=new_eps,
        )
        u = e.get("usage") or {}
        turns.append(dict(
            n=e.get("n"), clock=e.get("clock"), ch=e.get("channel"),
            user=e.get("user"), narr=e.get("narration"), reply=e.get("reply"),
            cl=locs.get(w.get("char_loc"), {}).get("name", w.get("char_loc")),
            ul=locs.get(w.get("user_loc"), {}).get("name", w.get("user_loc")),
            bat=w.get("battery"), chg=charging, power=flags.get("floor_power"), weather=flags.get("weather"),
            stage=stage_name, st=[stats.get(k, {}).get("value") for k in ("closeness", "trust", "flutter")],
            chips=chips, raw=raw,
            use=dict(i=u.get("input", 0), c=u.get("cached", 0), o=u.get("output", 0), s=round(u.get("seconds", 0), 1),
                     by=usage_rows(u.get("by_label"))),
        ))
    last = rows[-1]["state"] if rows else {}
    secrets = []
    for x in last.get("character", {}).get("secrets", []):
        leaked = x.get("revealed_turn") is not None and x.get("opened_turn") is None
        secrets.append(dict(id=x["id"], name=SECRET_NAMES.get(x["id"], x["id"]), status=x.get("status"),
                            opened=x.get("opened_turn"), revealed=x.get("revealed_turn"), leaked=leaked))
    promises = [dict(id=p["id"], name=PROMISE_NAMES.get(p["id"], p.get("text", p["id"])[:16]), status=p.get("status"))
                for p in last.get("relationship", {}).get("promises", [])]
    return turns, secrets, promises


# ---------------------------------------------------------------- 기준선 판
def baseline_run(rows):
    system = None
    turns = []
    for d in rows:
        if "system" in d and "n" not in d:
            system = d["system"]
            continue
        u = d.get("usage") or {}
        turns.append(dict(n=d.get("n"), user=d.get("user"), reply=d.get("reply"),
                          use=dict(i=u.get("input", 0), c=u.get("cached", 0), o=u.get("output", 0), s=round(u.get("seconds", 0), 1))))
    return turns, system


# ---------------------------------------------------------------- 시나리오
def scenario_info(first_engine_rows):
    world = load_yaml(SCENARIO_DIR / "world.yaml") or {}
    hist = load_yaml(SCENARIO_DIR / "history.yaml") or {}
    sc = world.get("scenario") or {}
    st0 = first_engine_rows[0]["state"] if first_engine_rows else {}
    if not sc:
        sc = st0.get("scenario", {})
    episodes = hist.get("episodes") or [x for x in st0.get("memory", {}).get("episodes", []) if x.get("prior")]
    rel = hist.get("relationship")
    if not rel:
        rel = parse_history_relationship(SCENARIO_DIR / "history.yaml")
    stages = {x["id"]: x["name"] for x in rel.get("stages", [])}
    stats = rel.get("stats", {})
    secrets = []
    for x in st0.get("character", {}).get("secrets", []):
        secrets.append(dict(id=x["id"], name=SECRET_NAMES.get(x["id"], x["id"]), cover=x.get("cover"), fact=x.get("fact")))
    return dict(
        title=sc.get("title", "14층의 야근"),
        intro=sc.get("intro") or [],
        setting=sc.get("setting"),
        appointment=sc.get("appointment"),
        countdown=sc.get("countdown"),
        start=world.get("start", "20:30"), end=world.get("end", "23:59"),
        episodes=[dict(id=x.get("id"), title=x.get("title"), when=x.get("when"),
                       line=EPISODE_LINES.get(x.get("id")) or (x.get("summary") or "").split(". ")[0]) for x in episodes],
        stage=stages.get(rel.get("stage"), rel.get("stage")),
        stages=[x["name"] for x in rel.get("stages", [])],
        stats=[dict(name=v.get("name", k), value=v.get("value")) for k, v in stats.items()],
        promises=[dict(name=PROMISE_NAMES.get(p.get("id"), p.get("id")), text=p.get("text"), by=p.get("by")) for p in rel.get("promises", [])],
        secrets=secrets,
    )


def parse_history_relationship(path):
    """PyYAML이 없을 때 history.yaml의 relationship 블록만 대충 읽는다."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    block = re.split(r"^episodes:", text, flags=re.M)[0]
    stages = [dict(id=m.group(1), name=m.group(2).strip()) for m in re.finditer(r"- \{id: (\w+), name: ([^,}]+)", block)]
    stage = re.search(r"^\s+stage: (\w+)", block, re.M)
    stats = {m.group(1): dict(name=m.group(2).strip(), value=int(m.group(3)))
             for m in re.finditer(r"^\s+(\w+):\s*\{name: ([^,]+), value: (\d+)\}", block, re.M)}
    promises = [dict(id=m.group(1), by=m.group(2), text=m.group(3))
                for m in re.finditer(r'- \{id: (\w+), by: (\S+?), episode: \w+, status: \w+, text: "(.*?)"\}', block)]
    return dict(stages=stages, stage=stage.group(1) if stage else None, stats=stats, promises=promises)


def summary_md_notes(md_path):
    """summary.md의 측정 약점 절(제목에 '약점')과 '맞음*' 각주를 그대로 가져온다."""
    try:
        lines = md_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = "약점" in line
            continue
        if line.startswith("맞음*"):
            out.append(line.strip())
        elif inside and line.startswith("- "):
            out.append(line[2:].strip())
    return out


# ---------------------------------------------------------------- 조립
def build(demo=False):
    runs_out = []
    first_engine_rows = None
    run_cfgs = [c for c in RUNS if c.get("group") == "수정 후" and c["turns"] == 15] if demo else RUNS
    out = HERE / "demo.html" if demo else OUT
    for cfg in run_cfgs:
        path = REPO / cfg["file"]
        if not path.exists():
            print(f"건너뜀(파일 없음): {cfg['file']}", file=sys.stderr)
            continue
        rows = read_jsonl(path)
        r = dict(id=cfg["id"], kind=cfg["kind"], turns=cfg["turns"], persona=cfg["persona"], label=cfg["label"],
                 group=cfg.get("group", "수정 전"), summary=cfg.get("summary", ""), file=cfg["file"])
        if cfg["kind"] == "engine":
            if first_engine_rows is None:
                first_engine_rows = rows
            turns, secrets, promises = engine_run(rows)
            r.update(rows=turns, secrets=secrets, promises=promises)
            clocks = [x["clock"] for x in turns if x.get("clock")]
            r["clock0"], r["clock1"] = (clocks[0], clocks[-1]) if clocks else (None, None)
            last = turns[-1] if turns else {}
            r["final"] = dict(stage=last.get("stage"), st=last.get("st"), power=last.get("power"), bat=last.get("bat"))
        else:
            turns, system = baseline_run(rows)
            r.update(rows=turns, system=system, clock0=None, clock1=None)
        r["n"] = len(r["rows"])
        runs_out.append(r)

    ids = {r["id"] for r in runs_out}
    highlights = []
    for h in (DEMO_HIGHLIGHTS if demo else HIGHLIGHTS):
        steps = [dict(run=a, n=b, cap=c) for a, b, c in h["steps"] if a in ids]
        if steps:
            highlights.append(dict(tone=h["tone"], title=h["title"], desc=h["desc"], steps=steps))

    summary, summary_notes, summary_src = None, [], None
    for p in SUMMARY_CANDIDATES:
        if p.exists():
            try:
                summary = json.loads(p.read_text(encoding="utf-8"))
                summary_src = str(p.relative_to(HERE.parent)) if HERE.parent in p.parents else str(p)
                summary_notes = summary_md_notes(p.with_suffix(".md"))
                print(f"요약 표: {p}")
            except (OSError, json.JSONDecodeError) as ex:
                print(f"요약 표를 읽지 못함({p}): {ex}", file=sys.stderr)
            break
    if demo and summary:
        summary = demo_summary(summary)

    personas = {k: dict(v, desc=v["desc"].split(" 50턴")[0]) for k, v in PERSONAS.items()} if demo else PERSONAS
    data = dict(demo=demo, scenario=scenario_info(first_engine_rows or []), personas=personas, runs=runs_out,
                highlights=highlights, summary=summary, summary_notes=summary_notes, summary_src=summary_src)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = TEMPLATE.read_text(encoding="utf-8")
    marker = "/*__DATA__*/null"
    if marker not in html:
        sys.exit("template.html에 /*__DATA__*/null 자리가 없다")
    html = html.replace(marker, blob, 1)
    out.write_text(html, encoding="utf-8")
    size = out.stat().st_size
    print(f"{out} {size/1024:.0f} KB · 판 {len(runs_out)}개 · 하이라이트 {len(highlights)}개")
    if size > 16 * 1024 * 1024:
        sys.exit("16MB를 넘었다")


def demo_summary(S):
    """요약 표에서 수정 후 15턴 열만 남긴다. 50턴 전용 표(구간별 토큰, 장기 기억 시험)는 뺀다."""
    col = "엔진 16턴 (a/b/c) 수정 후"
    out = {}
    t = S.get("table") or {}
    if col in t.get("columns", []):
        i = t["columns"].index(col)
        out["table"] = dict(t, columns=[col], rows=[dict(r, cells=[r["cells"][i]]) for r in t["rows"]])
    if col in (S.get("labels") or {}):
        out["labels"] = {col: S["labels"][col]}
    out["per_run"] = {k: v for k, v in (S.get("per_run") or {}).items() if k.startswith("F16-")}
    return out


if __name__ == "__main__":
    build(demo="--demo" in sys.argv[1:])
