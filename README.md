# 월드 모델 데이트 데모

캐릭터 채팅에 월드 모델을 붙인 봄날 놀이동산 하루. 판정과 사실은 코드가 정하고, LLM(`gemini-3.8-flash`)은 해석과 서술만 한다.

```
src/worldchat/
  world.py    시간·상태·장면 규칙 (LLM 없음)
  memory.py   사실 저장·검증, 판정이 요청할 때만 검색
  drama.py    불꽃놀이 타이밍 (LLM 없음, 입력만 LLM 판정)
  engine.py   턴 파이프라인, 프롬프트 조립, 이벤트 로그
  llm.py      Gemini 호출, 테스트용 목
  run.py      하루 전체 실행 (시뮬레이터 또는 직접 플레이)
  prompts/    버전별 프롬프트. 각 파일의 purpose가 그 버전의 목적
golden/       multi-turn golden. dev(튜닝용), holdout(검증용)
eval/         golden.py(teacher forcing), live_checks.py(실제 기록 검사), PROMPT_LOG.md
runs/         실제로 돈 대화의 이벤트 로그
demo/         로그만 읽어 만드는 턴별 2D HTML
```

## 실행

`.env`에 `GEMINI_API_KEY`를 넣는다.

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python eval/compare.py --samples 5                 # 월드 모델 있음/없음 비교 (--dry: 재생 검증만)
.venv/bin/python eval/compare_state.py --samples 3           # 지시 없이 상태만 준 조건 추가
.venv/bin/python -m pytest                                   # LLM 없는 판정 테스트
.venv/bin/python eval/golden.py --split dev --samples 5      # golden, 턴마다 정답으로 강제
.venv/bin/python eval/golden.py --dry                        # 코드 판정만 검사
.venv/bin/python eval/golden.py --calibrate                  # Judge 보정
.venv/bin/python -m worldchat.run --persona tsundere --human # 직접 플레이
.venv/bin/python -m worldchat.run --persona warm --plot confession --silent-noon
.venv/bin/python eval/live_checks.py runs/*.jsonl
.venv/bin/python demo/build_demo.py                          # demo/index.html 생성
```

튜닝 과정과 숫자의 한계는 `eval/PROMPT_LOG.md`에 있다.
