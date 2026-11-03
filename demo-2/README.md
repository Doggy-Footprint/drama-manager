# 데모 2: 스토리라인 엔진 (아카이브, 실패)

캐릭터 채팅에 드라마 매니저를 붙인 시도다. 장면 사건, 떡밥, 숨은 사정 공개, 기억 압축, 관계 기억을 시스템이 쥐고, 매 턴 LLM 판단을 거쳐 캐릭터 프롬프트를 조립했다. try1~try5와 직접 채팅까지 진행했고, 더 고치지 않고 참고용으로 둔다.

## 결과: 실패

**시스템에 의존하는 캐릭터 기억 및 상태 구성 + 캐릭터 정체성에서 LLM의 자연스러운 대화 진행 능력이 많이 소실됨.**

드러난 모습(2026-11-03 직접 채팅까지):
- 장면이 넘어가지 않는다. 캐릭터는 자기 말만 쓰고, 상태는 응답 뒤의 판단이 닫힌 목록으로 분류해야만 바뀐다. "떡볶이 집 가자"가 장소 목록과 이어지지 않아 5턴 동안 정문에서 "가자, 따라와"만 오갔다(`runs/live/1103_121818_structured.txt`). 장면을 넘기는 서술자가 없고, 분류가 빗나가면 기본값은 '변화 없음'이다. 작별(parted), 고백 뒤 관계 변화(branch)에서도 같은 구조의 문제가 있었다.
- 세계가 둘로 갈린다. 캐릭터는 빈칸을 상상으로 채우고(없는 가게, 지어낸 과거와 습관), 코드의 닫힌 세계는 그것을 받지 못한다.
- 멈춘 상태가 스스로를 붙잡는다. 장소가 그대로면 그 장소의 디테일과 사건 문장이 계속 들어와 같은 장면을 더 끈다.
- 판단이 응답 뒤라 한 턴 늦다.
- try5 기준선 비교: 캐릭터 호출만 한 기준선은 두 실행 모두 고백까지 갔고(비용 약 1/6), structured는 고백이 나오지 않았다.

마지막으로 넣은 장소 설명과 목적지 규칙(`park_v1.yaml` intro, `director_v7` move_to, `move_unresolved` 기록)은 이동 증상 하나만 덜어 준 수정이고 구조는 그대로다.

## 구성

```
src/storychat/
  engine.py    턴 파이프라인: 사건 발동, 떡밥, 숨은 사정 공개, 프롬프트 조립, 응답 뒤 판단 적용, 압축
  director.py  판단(review), 사건 문장 다시 쓰기(stage), 압축, 마감, 장면 초안
  memory.py    계층 에피소드, 오늘 요약, 관계 기억, 떡밥 원장
  world.py     시계, 장소, 배고픔, 기온
  llm.py       Claude 호출(생성), Gemini(채점), 테스트용 목
  play.py      턴 단위 명령(서브 에이전트 유저용), chat.py 직접 채팅
  prompts/     버전별 프롬프트. character_v4, director_v7, user_persona_v2가 마지막
  data/        카드(서진, 수아), 지난 기억, 고정 장면 park_v1
eval/          judge.py 집계와 채점, review_build.py 사람 평가 페이지
demo/          로그로 만드는 턴별 뷰어
runs/          try1~try5 실행 기록, live/ 직접 채팅 기록(커밋하지 않음), check5/ 점검 실행
```

## 실행

저장소 루트의 `.env`에 `ANTHROPIC_API_KEY`를 넣는다. 이 디렉터리(`demo-2/`)에서 루트의 `.venv`로 실행한다.

```bash
PYTHONPATH=src ../.venv/bin/python -m pytest -q                       # 60개
PYTHONPATH=src ../.venv/bin/python -m storychat.chat --debug          # 직접 채팅(판단 보기)
PYTHONPATH=src ../.venv/bin/python -m storychat.chat --mode baseline  # 캐릭터만
../.venv/bin/python eval/judge.py runs/try5/*.jsonl
```
