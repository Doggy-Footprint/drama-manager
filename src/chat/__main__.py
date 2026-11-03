"""캐릭터 프롬프트 하나만으로 대화한다(시스템 없음). 다음 시도의 세션이 생기기 전까지의 기본 세션이다.

  cd /Users/hwansu/today/261031/bunkerkids
  PYTHONPATH=src .venv/bin/python -m chat                       # 기본 캐릭터(수아)
  PYTHONPATH=src .venv/bin/python -m chat --system 파일.txt      # 다른 캐릭터 프롬프트
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from .llm import ClaudeLLM
from .terminal import run

DEFAULT_NAME = "수아"
DEFAULT_SYSTEM = """너는 수아다. 28살 여자 직장인이고, 두 달째 따로 만나는 또래 남자와 오늘 저녁 놀이동산에 왔다. 아직 사귀는 사이는 아니다.
성격: 말은 퉁명스럽지만 행동으로 챙긴다. 챙겨 주면서도 아닌 척한다. 자존심이 세서 먼저 사과하거나 먼저 마음을 말하기 어려워한다.
말투: 툭툭 던지는 반말. 말끝을 흐릴 때가 있다. 상대를 "너"라고 부른다.
마주 보고 하는 말의 길이로, 두세 문장으로 답한다. 몸을 움직일 일이 있으면 괄호 안에 네 동작을 짧게 적는다."""
OPENING = "(방금 놀이동산 정문에서 만났다. 네가 먼저 말을 건넨다)"


class PlainSession:
    def __init__(self, name: str, system: str) -> None:
        self.name, self.system = name, system
        self.llm = ClaudeLLM()
        self.calls = self.llm.calls
        self.messages: list[dict] = []

    def reply(self, user_text: str | None) -> str:
        self.messages.append({"role": "user", "content": user_text or OPENING})
        text = re.sub(r"\s*\n+\s*", " ", self.llm.chat(self.system, self.messages, label="character"))
        self.messages.append({"role": "assistant", "content": text})
        return text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", type=Path, help="캐릭터 시스템 프롬프트 파일")
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()
    system = args.system.read_text(encoding="utf-8").strip() if args.system else DEFAULT_SYSTEM
    run(PlainSession(args.name, system), intro=f"오늘 저녁, {args.name}와 놀이동산에 왔다.", label="plain")


if __name__ == "__main__":
    main()
