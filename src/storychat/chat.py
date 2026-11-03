"""터미널에서 서진과 직접 대화한다.

  cd /Users/hwansu/today/261031/bunkerkids
  PYTHONPATH=src .venv/bin/python -m storychat.chat                 # 구조 켬
  PYTHONPATH=src .venv/bin/python -m storychat.chat --mode baseline # 캐릭터만
  PYTHONPATH=src .venv/bin/python -m storychat.chat --debug         # 시스템 판단도 보기

대화 중 명령: /debug 판단 보기 켜고 끄기, /state 지금 상태, /quit 끝내기(Ctrl-D도 같다)
로그는 runs/live/<시각>_<모드>.jsonl에 남는다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .director import DIRECTOR_PROMPT
from .engine import CHARACTER_PROMPT, MODES, Engine, EventLog
from .llm import GENERATION_EFFORT, GENERATION_MODEL, ClaudeLLM
from .play import CARD, MAX_TURNS, RUNS, conclude, view
from .schema import hhmm

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
SHOWN = {"signals", "checkpoint_fired", "checkpoint_deferred", "hidden_revealed", "hidden_spoken", "detail_shown",
         "detail_planted", "detail_dropped", "payoff_injected", "frame_changed", "move_rejected", "memory_recalled",
         "relationship_updated", "episode_compressed", "today_summary_updated", "stage_rejected", "refused"}


def describe(e: dict) -> str | None:
    k = e["kind"]
    if k == "signals":
        bits = [b for b in ("긴장" if e["tension"] else "", f"분기:{e['branch']}" if e["branch"] else "",
                            f"화제:{','.join(e['topics'])}" if e["topics"] else "",
                            f"회상:{','.join(e['recall_small'])}" if e["recall_small"] else "") if b]
        return "판단 " + (" · ".join(bits) or "평온")
    text = {
        "checkpoint_fired": lambda: f"사건 {e['id']}: {e['core']}",
        "checkpoint_deferred": lambda: f"사건 미룸 {e['id']} (긴장 중)",
        "hidden_revealed": lambda: f"히든 떠오름 {e['id']}",
        "hidden_spoken": lambda: f"히든 말함 {e['id']}",
        "detail_shown": lambda: f"눈에 띔 {e['id']}: {e['text']}",
        "detail_planted": lambda: f"떡밥 살포 {e['id']}",
        "detail_dropped": lambda: f"떡밥 폐기 {e['id']}",
        "payoff_injected": lambda: f"떡밥 회수 {e['id']}: {e['core']}",
        "frame_changed": lambda: f"{'장소' if e['axis'] == 'place' else '관계'} {e['old']} → {e['new']}",
        "move_rejected": lambda: f"이동 거부 {e['proposed']}",
        "memory_recalled": lambda: f"기억 떠오름 {', '.join(e['smalls'])}",
        "relationship_updated": lambda: f"관계 기억 갱신: {e['state']}",
        "episode_compressed": lambda: f"압축 T{e['start']}~T{e['end']} {e['title']}",
        "today_summary_updated": lambda: f"오늘 요약: {e['after']}",
        "stage_rejected": lambda: f"사건 문장 다시 씀 {e['id']}",
        "refused": lambda: f"모델 거절 {e['label']}",
    }.get(k)
    return text() if text else None


def debug_lines(engine: Engine, start: int) -> str:
    lines = [describe(e) for e in engine.log.events[start:] if e["kind"] in SHOWN]
    return "\n".join(f"{DIM}  · {l}{RESET}" for l in lines if l)


def state_text(engine: Engine) -> str:
    s = engine.state
    hidden = ", ".join(f"{h['id']}({'말함' if h['id'] in engine.spoken else '떠오름'})" for h in engine.revealed) or "없음"
    ledger = ", ".join(f"{d.id}:{d.status}" for d in engine.ledger.foreshadows())
    return (f"{DIM}  시각 {hhmm(s.clock)} · {s.place} · {s.temperature} · {s.hunger} · 턴 {s.turn}/{MAX_TURNS}\n"
            f"  사이: {engine.relationship.state}\n  히든: {hidden}\n  떡밥: {ledger}\n"
            f"  지난 사건: {', '.join(sorted(engine.fired)) or '없음'}\n  오늘 요약: {engine.episodes.today_summary or '(아직 없음)'}{RESET}")


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

    print(f"{BOLD}오늘 저녁, 두 달째 따로 만나 온 {engine.card['name']}와 놀이동산에 왔다.{RESET}")
    print(engine.scene["intro"].strip())
    print(f"{DIM}모드: {args.mode} · /debug /state /quit · 로그 runs/{name}.jsonl{RESET}\n")

    said: str | None = None
    while True:
        start = len(engine.log.events)
        try:
            reply = engine.step(said)
        except KeyboardInterrupt:
            print("\n(응답을 기다리다 멈췄다)")
            break
        print(view(engine, reply))
        if debug:
            print(debug_lines(engine, start))
        if engine.state.ended or engine.state.turn >= MAX_TURNS:
            break
        while True:
            try:
                said = input(f"\n{BOLD}나:{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                said = "/quit"
                print()
            if said == "/debug":
                debug = args.mode == "structured" and not debug
                print(f"{DIM}  판단 보기 {'켬' if debug else '끔 (baseline에는 판단이 없다)'}{RESET}")
            elif said == "/state":
                print(state_text(engine))
            elif said:
                break
        if said == "/quit":
            break
    print(conclude(engine))
    if debug or args.mode == "structured":
        print(state_text(engine))
    calls = [e for e in engine.log.events if e["kind"] == "llm_call"]
    print(f"{DIM}  호출 {len(calls)}회, 약 ${sum(c['cost_usd'] for c in calls):.3f} · 로그 runs/{name}.jsonl{RESET}")


if __name__ == "__main__":
    sys.exit(main())
