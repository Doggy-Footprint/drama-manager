# drama-manager

캐릭터 하나와 하룻밤을 보내는 RP 엔진. **LLM은 판단하고 서술하고, 상태는 코드가 쥔다.**

기억을 전부 넣은 강한 기준선(단일 LLM)과 나란히 돌리고, 평가로 실패를 찾고, 턴 trace로 원인을 짚고, 고친 뒤 다시 쟀다.
자세한 설명과 그림은 [`ref/README.html`](ref/README.html), 평가 기록은 [`eval/README.md`](eval/README.md).

## 한 턴의 흐름

```
유저 입력 → 검색(병렬: 월드 · 떡밥 · 사실 추출) → 드라마 매니저(DM) → 코드 검증·반영 → 캐릭터 → 대사
```

- **DM**은 시간, 이벤트, 떡밥, 비밀 열기, 관계 변화를 JSON으로 *제안*만 한다.
- **코드**가 검증한다. 시간은 앞으로만, 이벤트 전제(`requires`)·효과(`effects`), 소지품·충전, 관계는 한 턴 ±1·단계 한 칸, 비밀은 locked → 열림. 거절·보정은 trace에 남는다.
- **캐릭터**는 코드가 골라 준 XML만 본다.
- 지난 만남의 디테일은 요약하지 않고 원문째 떡밥 풀에 넣고, 출처(지난 만남 / 오늘 밤 T#~T#)를 단다.

## 실행

`.env`에 `OPENAI_API_KEY`가 필요하다. 모델은 `RP_MODEL`(기본 `gpt-6-luna`), DM 추론 강도는 `RP_DM_EFFORT`(기본 `low`).

```bash
PYTHONPATH=src .venv/bin/python -m park            # http://127.0.0.1:8765, 시작 화면에서 이야기 선택
.venv/bin/python -m pytest -q

# 터미널(에이전트끼리 시험할 때)
PYTHONPATH=src .venv/bin/python -m park.cli new 이름 [--scenario overtime|park|hometown|surgeon] [--baseline]
PYTHONPATH=src .venv/bin/python -m park.cli say 이름 "말 (행동)" [--verbose]
```

턴마다의 판단 전체는 `runs/park/<시각>_<시나리오>.jsonl`에 남는다.
시나리오는 `src/park/scenarios/<id>/`의 `world.yaml`, `character.yaml`, `details.yaml`, (선택) `history.yaml`이다.

## 평가 요약 (야근 이야기 `overtime`, 엔진 vs 기준선)

시뮬레이션 유저 3종(A 적극적 · B 선을 긋는다 · C 미루고 떠본다) × 15턴/50턴.

| | 엔진 | 기준선 |
|---|---|---|
| 50턴 판 이벤트 수 | 9~10 | 0 (유저가 써 넣어야 일어남) |
| 금지 호칭 "채원아" | 0회 (10판) | 6회 (A 50턴) |
| 소등 뒤 충전·소지품 | 룰이 막고 추적 | 없던 물건이 나옴 |
| 턴당 입력 토큰 | T30 이후 35~37k에서 평탄 | 2.7k → 8.5k 계속 증가 |
| 지연 | 17~20초 | 3초대 |

평가 → 수정 → 재측정으로 고친 것:

- 잠긴 비밀이 먼저 샘 1/0/3 → 0/0/0 (목표 숨김 `after_secret`, 카피 초안을 공개 기억으로 분리)
- 강제한 비가 지문에 없음 0/4 → 3/3 (강제 이벤트 문장을 그 턴 지문에 붙임, core 이벤트는 늘 후보)
- 30턴 뒤 오늘 밤 문구를 틀림 0/2 → 2/2 (원문 디테일 보존 + 출처)

남은 문제: 관계가 너무 빨리 끝까지 참, 권유만으로 관계가 오름, 비밀이 일찍 열림, 비밀 경계를 프롬프트가 쥠, 비싸고 느림(호출당 고정 지연이 직렬 3단계로 쌓임).

표본은 판당 1회이고, 엔진과 기준선이 같은 대본을 받지 않았다.
