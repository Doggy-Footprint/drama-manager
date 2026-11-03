"""턴 단위로 데이트를 진행한다. 유저(사람 또는 서브 에이전트)는 이 명령의 출력만 본다.

  python -m storychat.play start warm_gentle --card warm --style gentle
  python -m storychat.play say warm_gentle "오늘 뭐 탈까"
  python -m storychat.play persona --style gentle

호출 사이의 상태는 runs/<이름>.state.pkl에 둔다. 출력에는 유저가 볼 수 있는 세계 사실만 나온다.
"""
from __future__ import annotations

import argparse
import pickle
from pathlib import Path

from .director import DIRECTOR_PROMPT, load_data, load_prompt
from .engine import Engine, EventLog
from .llm import GENERATION_EFFORT, GENERATION_MODEL, ClaudeLLM
from .schema import hhmm

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
MAX_TURNS = 50
USER_PERSONA = "user_persona_v1"


def state_path(name: str) -> Path:
    return RUNS / f"{name}.state.pkl"


def attach(engine: Engine, llm) -> None:
    engine.llm = llm
    engine.director.llm = llm


def save(engine: Engine, name: str) -> None:
    llm = engine.llm
    attach(engine, None)
    state_path(name).write_bytes(pickle.dumps(engine))
    attach(engine, llm)


def load(name: str) -> Engine:
    engine: Engine = pickle.loads(state_path(name).read_bytes())
    attach(engine, ClaudeLLM(on_call=lambda call: engine.log.emit("llm_call", **call)))
    return engine


def view(engine: Engine, reply: str) -> str:
    s = engine.state
    status = [hhmm(s.clock), s.place, s.temperature]
    if s.hunger == "배고픔":
        status.append("배고픔")
    lines = [f"[{' · '.join(status)}]"]
    lines += [f"방금: {fact}" for fact in engine.new_facts]
    lines.append(f"{engine.card['name']}: {reply}")
    return "\n".join(lines)


def conclude(engine: Engine) -> str:
    engine.close_storyline()
    calls = [e for e in engine.log.events if e["kind"] == "llm_call"]
    usage = {"calls": len(calls), **{k: sum(c[k] for c in calls) for k in ("input", "cache_read", "cache_write", "output", "cost_usd", "seconds")}}
    engine.log.emit("run_end", ended=engine.state.ended, usage={"engine": usage})
    if engine.state.turn >= MAX_TURNS and not engine.state.ended:
        return "[대화 턴 한도에 닿아 여기서 멈춘다]"
    return "[데이트가 끝났다]"


def advance(engine: Engine, name: str, said: str | None) -> str:
    reply = engine.step(said)
    out = view(engine, reply)
    if engine.state.ended or engine.state.turn >= MAX_TURNS:
        out += "\n" + conclude(engine)
    save(engine, name)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("name")
    start.add_argument("--card", required=True, choices=["warm", "tsundere"])
    start.add_argument("--style", required=True, choices=["gentle", "aloof", "fiery"])
    say = sub.add_parser("say")
    say.add_argument("name")
    say.add_argument("text")
    end = sub.add_parser("end")  # 유저가 말없이 떠났을 때 스토리라인을 닫는다
    end.add_argument("name")
    persona = sub.add_parser("persona")
    persona.add_argument("--style", required=True, choices=["gentle", "aloof", "fiery"])
    args = parser.parse_args()

    if args.command == "persona":
        prompt = load_prompt(USER_PERSONA)
        print(prompt["persona"].format(history=load_data("seed.yaml")["public_history"].strip(), style=prompt["styles"][args.style]))
    elif args.command == "start":
        log = EventLog(RUNS / f"{args.name}.jsonl")
        log.emit("run_meta", card=args.card, style=args.style, model=GENERATION_MODEL, effort=GENERATION_EFFORT, max_turns=MAX_TURNS,
                 user_source="claude-sonnet-5-5 subagent", prompts={"character": "character_v1", "director": DIRECTOR_PROMPT, "user": USER_PERSONA})
        engine = Engine(ClaudeLLM(on_call=lambda call: log.emit("llm_call", **call)), args.card, log=log)
        print(advance(engine, args.name, None))
    elif args.command == "say":
        engine = load(args.name)
        if engine.closed:
            print("[이미 끝난 데이트다]")
            return
        print(advance(engine, args.name, args.text))
    else:
        engine = load(args.name)
        if not engine.closed:
            print(conclude(engine))
            save(engine, args.name)


if __name__ == "__main__":
    main()
