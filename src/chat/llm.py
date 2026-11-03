"""Claude 호출과 호출별 사용량 기록. 터미널이 턴마다 호출 수, 토큰, KV 캐시 적중률을 보여 줄 때 이 기록을 읽는다."""
from __future__ import annotations

import time
from pathlib import Path

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
PRICE = {"input": 0.10, "cache_read": 0.01, "cache_write": 0.125, "output": 0.50}  # USD / MTok, 100K 토큰 이하 기준


class ClaudeLLM:
    def __init__(self, model: str = MODEL, effort: str = EFFORT) -> None:
        import anthropic
        from dotenv import dotenv_values

        key = dotenv_values(Path(__file__).resolve().parents[2] / ".env").get("ANTHROPIC_API_KEY")  # 저장소 루트의 .env
        if not key:
            raise RuntimeError(".env에 ANTHROPIC_API_KEY가 없습니다")
        self.client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=180.0)
        self.model, self.effort = model, effort
        self.calls: list[dict] = []  # 호출마다 하나. label, input, cache_read, cache_write, output, cost_usd, seconds

    def chat(self, system: str, messages: list[dict], *, label: str = "") -> str:
        """대화는 실제 멀티턴으로 보낸다. system과, 매 턴 바뀌는 마지막 메시지의 앞까지를 캐시한다."""
        sent = [dict(m) for m in messages]
        if len(sent) >= 2:
            sent[-2]["content"] = [{"type": "text", "text": sent[-2]["content"], "cache_control": {"type": "ephemeral"}}]
        started = time.time()
        response = self.client.messages.create(
            model=self.model, max_tokens=8000, output_config={"effort": self.effort}, messages=sent,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}])
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"응답이 끊김({label}): {response.stop_reason}")
        self._record(response.usage, label, time.time() - started)
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def _record(self, usage, label: str, seconds: float) -> None:
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = (usage.input_tokens * PRICE["input"] + read * PRICE["cache_read"] + write * PRICE["cache_write"]
                + usage.output_tokens * PRICE["output"]) / 1_000_000
        self.calls.append({"label": label, "input": usage.input_tokens, "cache_read": read, "cache_write": write,
                           "output": usage.output_tokens, "cost_usd": round(cost, 6), "seconds": round(seconds, 2)})
