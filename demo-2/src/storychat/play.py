"""턴 단위로 장면을 진행한다. 유저(사람 또는 서브 에이전트)는 이 명령의 출력만 본다.

  python -m storychat.play start try5/structured_fiery --style fiery --mode structured
  python -m storychat.play say try5/structured_fiery "오늘 뭐 탈까"
  python -m storychat.play end try5/structured_fiery
  python -m storychat.play persona --style fiery
  python -m storychat.play draft-scene park_v1   # 장면 초안을 만든다. 사람이 검수한 뒤 쓴다

호출 사이의 상태는 runs/<이름>.state.pkl에 둔다. 출력에는 유저가 볼 수 있는 세계 사실만 나온다.
"""
from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import yaml

from .director import BASE_TOPICS, DATA, DIRECTOR_PROMPT, Director, load_data, load_prompt
from .engine import CHARACTER_PROMPT, MODES, Engine, EventLog
from .llm import GENERATION_EFFORT, GENERATION_MODEL, ClaudeLLM
from .schema import hhmm

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
MAX_TURNS = 50
USER_PERSONA = "user_persona_v2"
CARD = "tsundere_some"
STYLES = list(load_prompt(USER_PERSONA)["styles"])


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
    said = f"{engine.card['name']}: {reply}"
    if engine.mode == "baseline":
        return said
    s = engine.state
    status = [hhmm(s.clock), s.place, s.temperature]
    if s.hunger == "배고픔":
        status.append("배고픔")
    return "\n".join([f"[{' · '.join(status)}]", *[f"방금: {fact}" for fact in engine.new_facts], said])


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


def draft_scene(name: str, card_name: str = CARD) -> None:
    """장면 초안을 만들어 파일로 쓴다. 이미 있는 장면은 덮어쓰지 않는다."""
    path = DATA / "scenes" / f"{name}.yaml"
    if path.exists():
        raise SystemExit(f"이미 있다: {path}")
    card = load_data("cards", f"{card_name}.yaml")
    seed = load_data(card["seed"])
    hidden = "\n".join(f"- {h['id']}: {h['fact']}" for h in card["hidden"])
    brief = (f"인물:\n{card['public']}\n둘의 지난 시간:\n{seed['public_history']}\n현재 사이: {seed['relationship']}\n"
             f"숨은 사정:\n{hidden}\n화제 목록: {', '.join(BASE_TOPICS)}")
    scene, problems = Director(ClaudeLLM()).scene_draft(brief)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = f"# {DIRECTOR_PROMPT} scene_draft 초안. 사람 검수 전이다.\n"
    path.write_text(head + yaml.safe_dump(scene, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
    print(path, "남은 문제:", problems or "없음")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("name")
    start.add_argument("--card", default=CARD, choices=[CARD])
    start.add_argument("--style", required=True, choices=STYLES)
    start.add_argument("--mode", default="structured", choices=MODES)
    say = sub.add_parser("say")
    say.add_argument("name")
    say.add_argument("text")
    end = sub.add_parser("end")  # 유저가 말없이 떠났을 때 스토리라인을 닫는다
    end.add_argument("name")
    persona = sub.add_parser("persona")
    persona.add_argument("--style", required=True, choices=STYLES)
    draft = sub.add_parser("draft-scene")
    draft.add_argument("name")
    args = parser.parse_args()

    if args.command == "persona":
        prompt = load_prompt(USER_PERSONA)
        card = load_data("cards", f"{CARD}.yaml")
        print(prompt["persona"].format(history=load_data(card["seed"])["public_history"].strip(),
                                       scene=load_data("scenes", f"{card['scene']}.yaml")["intro"].strip(),
                                       style=prompt["styles"][args.style]))
    elif args.command == "draft-scene":
        draft_scene(args.name)
    elif args.command == "start":
        log = EventLog(RUNS / f"{args.name}.jsonl")
        log.emit("run_meta", card=args.card, style=args.style, mode=args.mode, model=GENERATION_MODEL, effort=GENERATION_EFFORT, max_turns=MAX_TURNS,
                 user_source="claude-sonnet-5-5 subagent", prompts={"character": CHARACTER_PROMPT, "director": DIRECTOR_PROMPT, "user": USER_PERSONA})
        engine = Engine(ClaudeLLM(on_call=lambda call: log.emit("llm_call", **call)), args.card, mode=args.mode, log=log)
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
