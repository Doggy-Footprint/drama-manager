"""OpenAI 호출과 호출별 사용량 기록. 화면은 이 기록으로 턴마다 호출 수, 토큰, 캐시 적중, 걸린 시간을 보여 준다.

모델은 .env의 RP_MODEL로 바꿀 수 있다(기본 gpt-6-luna. gpt-6.1-luna는 이 키로 접근이 안 된다).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV = dotenv_values(ROOT / ".env")
MODEL = os.environ.get("RP_MODEL") or ENV.get("RP_MODEL") or "gpt-6-luna"


class LLM:
    def __init__(self, model: str = MODEL) -> None:
        from openai import OpenAI

        key = ENV.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(".env에 OPENAI_API_KEY가 없습니다")
        self.client = OpenAI(api_key=key, max_retries=3, timeout=180.0)
        self.model = model
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def json(self, system: str, user: str, schema: dict, *, label: str, effort: str = "low") -> dict:
        """system과 user 한 덩어리를 보내고 스키마에 맞는 JSON을 받는다. 앞부분이 같으면 서버가 알아서 캐시한다."""
        started = time.time()
        response = self.client.responses.create(
            model=self.model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            reasoning={"effort": effort},
            prompt_cache_key=f"park-{label}",
            text={"format": {"type": "json_schema", "name": label.replace("-", "_"), "strict": True, "schema": schema}},
        )
        self._record(response.usage, label, time.time() - started)
        return json.loads(response.output_text)

    def text(self, system: str, messages: list[dict], *, label: str, effort: str = "low") -> str:
        """실제 멀티턴으로 보내고 글을 받는다. 기준선(캐릭터 프롬프트만)이 쓴다."""
        started = time.time()
        response = self.client.responses.create(
            model=self.model, input=[{"role": "system", "content": system}, *messages], reasoning={"effort": effort})
        self._record(response.usage, label, time.time() - started)
        return response.output_text.strip()

    def _record(self, usage, label: str, seconds: float) -> None:
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        with self._lock:
            self.calls.append({"label": label, "input": usage.input_tokens, "cached": cached,
                               "output": usage.output_tokens, "seconds": round(seconds, 2)})
