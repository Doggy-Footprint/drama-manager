"""터미널에서 캐릭터와 직접 대화하는 루프. 세션은 reply()와 calls만 있으면 붙는다.

- 한글 입력: 터미널에서는 prompt_toolkit으로 받는다(기본 input()은 macOS에서 한글을 지울 때 깨진다). 파이프 입력은 input().
- 끝내기: /quit, !종료, Ctrl-D
- 턴마다 LLM 호출 수, 토큰, KV 캐시 적중률(캐시 읽기 토큰 / 전체 입력 토큰, API usage 값 그대로)을 보여 준다.
- runs/live/<시각>.txt에 사람이 읽는 대화록을 한 줄씩 바로 남긴다.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[2]
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
ANSI_CODE = re.compile(r"\033\[[0-9;]*m")
QUIT = {"/quit", "!종료"}
PROMPT = f"\n{BOLD}나:{RESET} "


class Session(Protocol):
    name: str          # 캐릭터 이름. 대사 앞에 붙는다
    calls: list[dict]  # LLM 호출 기록(chat.llm.ClaudeLLM.calls 형식). 터미널은 읽기만 한다

    def reply(self, user_text: str | None) -> str:
        """유저 말에 대한 캐릭터의 답. None이면 캐릭터가 먼저 말을 건넨다."""
        ...


def prompt_tokens(c: dict) -> int:
    return c["input"] + c["cache_read"] + c["cache_write"]


def hit(c: dict) -> str:
    total = prompt_tokens(c)
    return f"{100 * c['cache_read'] / total:.0f}%" if total else "-"


def totals(calls: list[dict]) -> dict:
    return {k: sum(c[k] for c in calls) for k in ("input", "cache_read", "cache_write", "output", "cost_usd")}


def usage_lines(calls: list[dict]) -> list[str]:
    if not calls:
        return []
    total = totals(calls)
    lines = [f"  · 이번 턴 LLM {len(calls)}회 · 토큰 {prompt_tokens(total) + total['output']:,}"
             f" (입력 {prompt_tokens(total):,}, 출력 {total['output']:,}) · 캐시 적중 {hit(total)}"]
    lines += [f"      {c['label']:<10} 입력 {prompt_tokens(c):>6,} (캐시 읽기 {c['cache_read']:,}, 쓰기 {c['cache_write']:,},"
              f" 그냥 {c['input']:,}) · 출력 {c['output']:>5,} · 적중 {hit(c)}" for c in calls]
    return lines


class Transcript:
    """사람이 읽는 대화록. 중간에 꺼져도 남도록 한 줄씩 바로 쓴다."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.file = path.open("a", encoding="utf-8")

    def write(self, text: str) -> None:
        self.file.write(ANSI_CODE.sub("", text) + "\n")
        self.file.flush()


_input_session = None


def read_line() -> str:
    global _input_session
    if not sys.stdin.isatty():
        return input(PROMPT)
    if _input_session is None:
        from prompt_toolkit import PromptSession

        _input_session = PromptSession()
    from prompt_toolkit.formatted_text import ANSI

    return _input_session.prompt(ANSI(PROMPT))


def run(session: Session, *, intro: str = "", label: str = "chat", max_turns: int = 50) -> Path:
    transcript = Transcript(ROOT / "runs" / "live" / f"{datetime.now():%m%d_%H%M%S}_{label}.txt")

    def show(text: str, *, dimmed: bool = False) -> None:
        print(f"{DIM}{text}{RESET}" if dimmed else text)
        transcript.write(text)

    if intro:
        show(intro)
    print(f"{DIM}/quit 또는 !종료로 끝낸다 · 기록 {transcript.path.relative_to(ROOT)}{RESET}")
    transcript.write(f"{label} · {datetime.now():%Y-%m-%d %H:%M}")

    said: str | None = None
    for _ in range(max_turns):
        start = len(session.calls)
        try:
            reply = session.reply(said)
        except KeyboardInterrupt:
            print("\n(응답을 기다리다 멈췄다)")
            break
        show(f"\n{session.name}: {reply}")
        for line in usage_lines(session.calls[start:]):
            show(line, dimmed=True)
        try:
            said = ""
            while not said:
                said = read_line().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if said in QUIT:
            break
        transcript.write(f"\n나: {said}")
    else:
        show("\n[대화 턴 한도에 닿아 여기서 멈춘다]")

    total = totals(session.calls)
    show(f"  전체 호출 {len(session.calls)}회, 캐시 적중 {hit(total)}, 약 ${total['cost_usd']:.3f}"
         f" · 기록 {transcript.path.relative_to(ROOT)}", dimmed=True)
    return transcript.path
