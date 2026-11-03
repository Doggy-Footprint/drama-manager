# bunkerkids

- `demo-1/`: 월드 모델 데이트 데모 (아카이브). 실행법은 `demo-1/README.md`.
- `demo-2/`: 스토리라인 엔진 데모 (아카이브, 실패). 실패 원인과 실행법은 `demo-2/README.md`.
- `plan.md`: 다음 시도.
- `src/chat/`: 터미널 채팅 인터페이스(데모 2에서 떼어 냄). 한글 입력, `/quit`·`!종료`, 턴마다 LLM 호출 수·토큰·KV 캐시 적중률, `runs/live/`에 대화록. 다음 시도의 세션은 `reply()`와 `calls`만 갖추면 `chat.terminal.run()`에 붙는다.

```bash
PYTHONPATH=src .venv/bin/python -m chat   # 캐릭터 프롬프트만으로 대화(수아)
.venv/bin/python -m pytest -q
```
