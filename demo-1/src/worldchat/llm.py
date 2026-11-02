"""LLM 호출 계층. 다른 모듈은 LLM 프로토콜만 알므로 목으로 바꿔 단독 실행할 수 있다."""
from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DEFAULT_MODEL = "gemini-3.8-flash"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, i: int, o: int, c: int) -> None:
        with self._lock:
            self.input_tokens += i
            self.output_tokens += o
            self.cached_tokens += c
            self.calls += 1


class LLM(Protocol):
    def text(self, system: str, messages: list[dict], *, label: str = "") -> str: ...
    def json(self, system: str, messages: list[dict], schema: dict, *, label: str = "") -> dict: ...


class GeminiLLM:
    def __init__(self, model: str = DEFAULT_MODEL, thinking_level: str = "low") -> None:
        from dotenv import load_dotenv
        from google import genai

        load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # 저장소 루트의 .env
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(".env에 GEMINI_API_KEY가 없습니다")
        self.client = genai.Client(api_key=key)
        self.model = model
        self.thinking_level = thinking_level
        self.usage = Usage()

    def _config(self, system: str, **extra):
        from google.genai import types

        return types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=2048,
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level),
            **extra,
        )

    def _call(self, system: str, messages: list[dict], **extra):
        from google.genai import errors, types

        contents = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["text"])])
            for m in messages
        ]
        for attempt in range(6):
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=contents, config=self._config(system, **extra)
                )
            except errors.APIError as exc:
                retryable = exc.code in (429, 500, 502, 503, 504)
                if not retryable or attempt == 5:
                    raise
                time.sleep(2**attempt + random.random())
                continue
            meta = response.usage_metadata
            if meta:
                self.usage.add(
                    meta.prompt_token_count or 0,
                    meta.candidates_token_count or 0,
                    meta.cached_content_token_count or 0,
                )
            if response.text:
                return response
            if attempt == 5:
                reason = response.candidates[0].finish_reason if response.candidates else "no candidates"
                raise RuntimeError(f"빈 응답: {reason}")
        raise RuntimeError("unreachable")

    def text(self, system: str, messages: list[dict], *, label: str = "") -> str:
        return self._call(system, messages).text.strip()

    def json(self, system: str, messages: list[dict], schema: dict, *, label: str = "") -> dict:
        response = self._call(system, messages, response_mime_type="application/json", response_json_schema=schema)
        return json.loads(response.text)


class ScriptedLLM:
    """테스트용 목. label별로 미리 정한 응답을 순서대로 돌려준다."""

    def __init__(self, responses: dict[str, list]) -> None:
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict] = []

    def _next(self, label: str, default):
        queue = self.responses.get(label)
        return queue.pop(0) if queue else default

    def text(self, system: str, messages: list[dict], *, label: str = "") -> str:
        self.calls.append({"label": label, "system": system, "messages": messages})
        return self._next(label, "(목 응답)")

    def json(self, system: str, messages: list[dict], schema: dict, *, label: str = "") -> dict:
        self.calls.append({"label": label, "system": system, "messages": messages})
        return self._next(label, {})
