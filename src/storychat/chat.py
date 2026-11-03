"""터미널에서 캐릭터(기본: 여자 버전 카드의 수아)와 직접 대화한다.

  cd /Users/hwansu/today/261031/bunkerkids
  PYTHONPATH=src .venv/bin/python -m storychat.chat                 # 구조 켬
  PYTHONPATH=src .venv/bin/python -m storychat.chat --mode baseline # 캐릭터만
  PYTHONPATH=src .venv/bin/python -m storychat.chat --debug         # 시스템 판단도 보기

대화 중 명령: /debug 판단 보기 켜고 끄기, /state 지금 상태, /quit 끝내기(Ctrl-D도 같다)
기록은 runs/live/<시각>_<모드>.jsonl(전체 이벤트)과 .txt(사람이 읽는 대화록, 판단 줄 포함)에 남는다.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from .director import DIRECTOR_PROMPT
from .engine import CHARACTER_PROMPT, MODES, Engine, EventLog
from .llm import GENERATION_EFFORT, GENERATION_MODEL, ClaudeLLM
from .play import CARD, MAX_TURNS, RUNS, conclude, view
from .schema import hhmm

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
SHOWN = {"signals", "checkpoint_fired", "checkpoint_deferred", "hidden_revealed", "hidden_spoken", "detail_shown",
         "detail_planted", "detail_dropped", "payoff_injected", "frame_changed", "move_rejected", "memory_recalled",
         "relationship_updated", "episode_compressed", "today_summary_updated", "stage_rejected", "refused"}


LEGEND = """판단 보기 읽는 법 (· 줄은 대사 밑에 붙는다)
  판단(다음 턴 반영)  방금 대사를 시스템이 읽은 결과. 이번 대사에는 영향이 없고 다음 대사부터 쓰인다
    긴장       둘 사이에 풀리지 않은 긴장이 있다. 긴장 중에는 미룰 수 있는 사건을 늦춘다
    분기       사이가 갈리는 순간(고백, 관계 변화 등)
    화제       지금 오가는 이야기. 숨은 사정이 열리는 조건에 쓰인다
  기억 떠오름  지난 기억 하나를 다음 몇 턴 동안 캐릭터에게 보여 준다. [큰 기억 / 작은 기억] 뒤에 내용
  사건         정해진 시각이나 장소에 세계에 일어난 일. 캐릭터와 나 모두 겪는다
  히든 떠오름  캐릭터가 숨긴 사정을 이제 말해도 되는 상태가 됐다. 말할지는 캐릭터가 정한다
  히든 말함    캐릭터가 숨긴 사정을 실제로 털어놓았다
  떡밥         눈에 띔(캐릭터에게 보여 줌) → 살포(캐릭터가 입에 올림) → 회수(나중에 세계가 되받음)
  관계 기억    둘의 사이를 적어 둔 문장이 바뀌었다. 바뀐 문장이 이후 대사의 기준이 된다"""


def describe(e: dict, engine: Engine) -> str | None:
    k = e["kind"]
    if k == "signals":
        bits = [b for b in ("긴장 있음" if e["tension"] else "", f"분기 {e['branch']}" if e["branch"] else "",
                            f"화제 {', '.join(e['topics'])}" if e["topics"] else "") if b]
        return "판단(다음 턴 반영): " + (" · ".join(bits) or "평온")
    if k == "memory_recalled":
        lines = []
        for small_id in e["smalls"]:
            big = next((b for b in engine.episodes.bigs.values() if any(s.id == small_id for s in b.smalls)), None)
            small = engine.episodes.small(small_id)
            lines.append(f"기억 떠오름(다음 턴부터): [{big.title if big else '?'} / {small.title}] {small.summary}" if small
                         else f"기억 떠오름 {small_id}")
        return "\n".join(lines)
    hidden = {h["id"]: h for h in engine.card["hidden"]}
    text = {
        "checkpoint_fired": lambda: f"사건 {e['id']}: {e['core']}",
        "checkpoint_deferred": lambda: f"사건 미룸 {e['id']} (긴장 중이라 늦춘다)",
        "hidden_revealed": lambda: f"히든 떠오름 {e['id']}: {hidden[e['id']]['fact']}",
        "hidden_spoken": lambda: f"히든 말함 {e['id']}",
        "detail_shown": lambda: f"떡밥 눈에 띔 {e['id']}: {e['text']}",
        "detail_planted": lambda: f"떡밥 살포 {e['id']}: \"{e['quote']}\"",
        "detail_dropped": lambda: f"떡밥 폐기 {e['id']} (입에 올리기 전에 회수 조건이 지나갔다)",
        "payoff_injected": lambda: f"떡밥 회수 {e['id']}: {e['core']}",
        "frame_changed": lambda: f"{'장소' if e['axis'] == 'place' else '관계'} {e['old']} → {e['new']}",
        "move_rejected": lambda: f"이동 거부 {e['proposed']} (갈 수 있는 곳 목록에 없다)",
        "relationship_updated": lambda: f"관계 기억 갱신: {e['before']} → {e['state']}",
        "episode_compressed": lambda: f"압축 T{e['start']}~T{e['end']} {e['title']} (지난 대화를 기억으로 접음)",
        "today_summary_updated": lambda: f"오늘 요약: {e['after']}",
        "stage_rejected": lambda: f"사건 문장 다시 씀 {e['id']}",
        "refused": lambda: f"모델 거절 {e['label']}",
    }.get(k)
    return text() if text else None


def debug_lines(engine: Engine, start: int) -> list[str]:
    lines = [describe(e, engine) for e in engine.log.events[start:] if e["kind"] in SHOWN]
    return [f"  · {part}" for l in lines if l for part in l.split("\n")]


def dim(lines: list[str]) -> str:
    return "\n".join(f"{DIM}{l}{RESET}" for l in lines)


ANSI_CODE = re.compile(r"\033\[[0-9;]*m")


class Transcript:
    """사람이 읽는 대화록. 판단 보기를 꺼도 판단 줄까지 남긴다. 한 줄씩 바로 써서 중간에 꺼져도 남는다."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", encoding="utf-8")

    def write(self, text: str) -> None:
        self.file.write(ANSI_CODE.sub("", text) + "\n")
        self.file.flush()


def state_text(engine: Engine) -> str:
    s = engine.state
    hidden = ", ".join(f"{h['id']}({'말함' if h['id'] in engine.spoken else '떠오름'})" for h in engine.revealed) or "없음"
    ledger = ", ".join(f"{d.id}:{d.status}" for d in engine.ledger.foreshadows())
    return (f"{DIM}  시각 {hhmm(s.clock)} · {s.place} · {s.temperature} · {s.hunger} · 턴 {s.turn}/{MAX_TURNS}\n"
            f"  사이: {engine.relationship.state}\n  히든: {hidden}\n  떡밥: {ledger}\n"
            f"  지난 사건: {', '.join(sorted(engine.fired)) or '없음'}\n  오늘 요약: {engine.episodes.today_summary or '(아직 없음)'}{RESET}")


PROMPT = f"\n{BOLD}나:{RESET} "
_session = None


def read_line() -> str:
    """터미널에서는 prompt_toolkit으로 받는다. 기본 input()은 macOS에서 한글을 지울 때 바이트 단위로 깨진다.
    입력을 파이프로 넣을 때는 input()을 쓴다."""
    global _session
    if not sys.stdin.isatty():
        return input(PROMPT)
    if _session is None:
        from prompt_toolkit import PromptSession

        _session = PromptSession()
    from prompt_toolkit.formatted_text import ANSI

    return _session.prompt(ANSI(PROMPT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="structured", choices=MODES)
    parser.add_argument("--card", default="tsundere_some_f", choices=["tsundere_some_f", CARD])
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    debug = args.debug and args.mode == "structured"
    name = f"live/{datetime.now():%m%d_%H%M%S}_{args.mode}"
    log = EventLog(RUNS / f"{name}.jsonl")
    log.emit("run_meta", card=args.card, style="human", mode=args.mode, model=GENERATION_MODEL, effort=GENERATION_EFFORT, max_turns=MAX_TURNS,
             user_source="human", prompts={"character": CHARACTER_PROMPT, "director": DIRECTOR_PROMPT, "user": None})
    engine = Engine(ClaudeLLM(on_call=lambda call: log.emit("llm_call", **call)), args.card, mode=args.mode, log=log)
    transcript = Transcript(RUNS / f"{name}.txt")

    def show(text: str, *, dimmed: bool = False) -> None:
        print(f"{DIM}{text}{RESET}" if dimmed else text)
        transcript.write(text)

    show(f"{BOLD}오늘 저녁, 두 달째 따로 만나 온 {engine.card['name']}와 놀이동산에 왔다.{RESET}")
    show(engine.scene["intro"].strip())
    print(f"{DIM}모드: {args.mode} · /debug /state /quit · 기록 runs/{name}.txt{RESET}")
    transcript.write(f"모드: {args.mode} · 카드 {args.card} · {datetime.now():%Y-%m-%d %H:%M}")
    if debug:
        print(f"{DIM}{LEGEND}{RESET}")
    print()

    said: str | None = None
    while True:
        start = len(engine.log.events)
        try:
            reply = engine.step(said)
        except KeyboardInterrupt:
            print("\n(응답을 기다리다 멈췄다)")
            break
        show("\n" + view(engine, reply))
        lines = debug_lines(engine, start)
        if debug and lines:
            print(dim(lines))
        for line in lines:
            transcript.write(line)
        if engine.state.ended or engine.state.turn >= MAX_TURNS:
            break
        while True:
            try:
                said = read_line().strip()
            except (EOFError, KeyboardInterrupt):
                said = "/quit"
                print()
            if said == "/debug":
                debug = args.mode == "structured" and not debug
                print(f"{DIM}  판단 보기 {'켬' if debug else '끔 (baseline에는 판단이 없다)'}{RESET}")
                if debug:
                    print(f"{DIM}{LEGEND}{RESET}")
            elif said == "/state":
                print(state_text(engine))
            elif said:
                break
        if said == "/quit":
            break
        transcript.write(f"\n나: {said}")
    start = len(engine.log.events)
    show("\n" + conclude(engine))
    for line in debug_lines(engine, start):
        transcript.write(line)
    if args.mode == "structured":
        show(state_text(engine), dimmed=False)
    calls = [e for e in engine.log.events if e["kind"] == "llm_call"]
    show(f"{DIM}  호출 {len(calls)}회, 약 ${sum(c['cost_usd'] for c in calls):.3f} · 기록 runs/{name}.txt, runs/{name}.jsonl{RESET}")


if __name__ == "__main__":
    sys.exit(main())
