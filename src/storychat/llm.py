"""LLM 호출 계층. 나머지 코드는 chat/text/json 세 메서드만 안다. 생성은 Anthropic API로 부른다."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

GENERATION_MODEL = "claude-haiku-5-5"
GENERATION_EFFORT = "medium"
JUDGE_MODEL = "gemini-3.8-flash"
PRICE = {"input": 0.10, "cache_read": 0.01, "cache_write": 0.125, "output": 0.50}  # USD / MTok, 100K 토큰 이하 기준


class RefusalError(RuntimeError):
    """모델이 응답을 거절했다. 숨기지 않고 로그에 남기기 위해 일반 오류와 구분한다."""


class LLM(Protocol):
    def chat(self, system: str, messages: list[dict], *, label: str = "") -> str: ...
    def text(self, system: str, prompt: str, *, label: str = "") -> str: ...
    def json(self, system: str, prompt: str, schema: dict, *, label: str = "") -> dict: ...


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("calls", "input_tokens", "cache_read", "cache_write", "output_tokens", "cost_usd", "seconds")}


def strict_schema(schema):
    """구조화 출력은 모든 객체에 additionalProperties: false를 요구한다."""
    if isinstance(schema, dict):
        out = {k: strict_schema(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema


class ClaudeLLM:
    def __init__(self, model: str = GENERATION_MODEL, effort: str = GENERATION_EFFORT,
                 on_call: Callable[[dict], None] | None = None) -> None:
        import anthropic
        from dotenv import dotenv_values

        key = dotenv_values(Path(__file__).resolve().parents[2] / ".env").get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(".env에 ANTHROPIC_API_KEY가 없습니다")
        self.client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=180.0)
        self.model, self.effort, self.on_call = model, effort, on_call
        self.usage = Usage()

    def _call(self, system: str, messages: list[dict], schema: dict | None, label: str) -> str:
        config: dict = {"effort": self.effort}
        if schema is not None:
            config["format"] = {"type": "json_schema", "schema": strict_schema(schema)}
        started = time.time()
        response = self.client.messages.create(
            model=self.model, max_tokens=8000, output_config=config, messages=messages,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}])
        if response.stop_reason == "refusal":
            raise RefusalError(label)
        if response.stop_reason == "max_tokens":
            raise RuntimeError(f"max_tokens에 닿음({label})")
        self._record(response.usage, label, time.time() - started)
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def _record(self, usage, label: str, seconds: float) -> None:
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = (usage.input_tokens * PRICE["input"] + read * PRICE["cache_read"] + write * PRICE["cache_write"]
                + usage.output_tokens * PRICE["output"]) / 1_000_000
        call = {"label": label, "input": usage.input_tokens, "cache_read": read, "cache_write": write,
                "output": usage.output_tokens, "cost_usd": round(cost, 6), "seconds": round(seconds, 2)}
        with self.usage._lock:
            u = self.usage
            u.calls += 1
            u.input_tokens += usage.input_tokens
            u.cache_read += read
            u.cache_write += write
            u.output_tokens += usage.output_tokens
            u.cost_usd += cost
            u.seconds += seconds
        if self.on_call:
            self.on_call(call)

    def chat(self, system: str, messages: list[dict], *, label: str = "") -> str:
        """대화는 실제 멀티턴으로 보낸다. 마지막 메시지는 매 턴 달라지므로 그 앞까지 캐시한다."""
        sent = [dict(m) for m in messages]
        if len(sent) >= 2:
            sent[-2]["content"] = [{"type": "text", "text": sent[-2]["content"], "cache_control": {"type": "ephemeral"}}]
        return self._call(system, sent, None, label)

    def text(self, system: str, prompt: str, *, label: str = "") -> str:
        return self._call(system, [{"role": "user", "content": prompt}], None, label)

    def json(self, system: str, prompt: str, schema: dict, *, label: str = "") -> dict:
        return json.loads(self._call(system, [{"role": "user", "content": prompt}], schema, label))


class GeminiJudgeLLM:
    """평가 전용. 생성과 다른 계열의 모델로 채점한다."""

    def __init__(self, model: str = JUDGE_MODEL) -> None:
        from dotenv import load_dotenv
        from google import genai

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(".env에 GEMINI_API_KEY가 없습니다")
        self.client, self.model = genai.Client(api_key=key), model

    def json(self, system: str, prompt: str, schema: dict, *, label: str = "") -> dict:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=8192, response_mime_type="application/json",
            response_json_schema=schema, thinking_config=types.ThinkingConfig(thinking_level="low"))
        for attempt in range(6):
            try:
                response = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
            except errors.APIError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 5:
                    raise
                time.sleep(2 ** attempt)
                continue
            if response.text:
                return json.loads(response.text)
        raise RuntimeError(f"Judge 빈 응답({label})")


class ScriptedLLM:
    """테스트용 목. label별 응답을 순서대로 돌려주고 호출을 기록한다."""

    def __init__(self, responses: dict[str, list] | None = None) -> None:
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.calls: list[dict] = []
        self.usage = Usage()

    def _next(self, label: str, system: str, prompt: str, default):
        self.calls.append({"label": label, "system": system, "prompt": prompt})
        queue = self.responses.get(label)
        item = queue.pop(0) if queue else default
        if isinstance(item, Exception):
            raise item
        return item

    def chat(self, system: str, messages: list[dict], *, label: str = "") -> str:
        rendered = "\n".join(m["content"] for m in messages)
        reply = self._next(label, system, rendered, "(목 응답)")
        self.calls[-1]["messages"] = messages
        return reply

    def text(self, system: str, prompt: str, *, label: str = "") -> str:
        return self._next(label, system, prompt, "(목 응답)")

    def json(self, system: str, prompt: str, schema: dict, *, label: str = "") -> dict:
        return self._next(label, system, prompt, {})
