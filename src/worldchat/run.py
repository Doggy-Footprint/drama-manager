"""하루 전체를 실제로 돌려 이벤트 로그를 남긴다.

  python -m worldchat.run --persona warm --plot confession   # 유저 시뮬레이터
  python -m worldchat.run --persona tsundere --human         # 직접 플레이
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import world as w
from .engine import DEFAULT_ANALYST, DEFAULT_CHARACTER, Engine, EventLog, load_yaml
from .llm import DEFAULT_MODEL, GeminiLLM

ROOT = Path(__file__).resolve().parents[2]
MAX_TURNS = 60
THIN_LINE = "봄이라 산뜻하게. 좀 얇나?"


def beat(state: w.WorldState, plot: str, silent_noon: bool) -> str | None:
    """시뮬레이터에게 줄 이번 턴 방향. 유저 차례의 상태만 보고 정한다. None이면 침묵한다."""
    clock = state.clock
    if state.scene == "만남":
        return "들어가자고 하며 먼저 타고 싶은 놀이기구를 말한다."
    if state.scene == "놀이동산" and state.episode <= 1:
        if clock >= w.LUNCH_AT and silent_noon and state.meal_proposed_at is None:
            return None
        if state.meal_proposed_at is not None:
            return "상대의 식사 제안을 받아들이며 파스타를 먹자고 한다."
        if clock >= w.LUNCH_AT - 25:
            return "요즘 빠져 있는 드라마 이야기를 신나게 이어 간다. 먹는 이야기는 꺼내지 않는다."
        return "다음 놀이기구를 타자고 하거나, 방금 탄 놀이기구 소감을 말하며 다음 것을 고른다."
    if state.scene == "식당":
        if state.sauce == "armed":
            return "뭐가 묻었는지 몰라 어리둥절하게 왜 그러냐고 묻는다."
        if state.sauce == "done":
            return "닦아 준 것에 쑥스러워하며 고맙다고 하고, 오후에 뭘 할지 말한다."
        return "음식과 가게 분위기 이야기를 한다."
    if state.scene == "놀이동산":
        if clock >= w.EVENING_AT - 30:
            return "해가 지기 시작한다고 말하며 이제 좀 걷자고 한다. 놀이기구 이야기는 하지 않는다."
        ride = ("바이킹", "범퍼카", "관람차", "귀신의 집")[state.turn % 4]
        return f"방금 재밌었다고 짧게 말하고 {ride}을(를) 타러 가자고 한다."
    if state.scene == "산책":
        if state.fireworks == "deferred":
            if state.deferred_turns >= 2:
                return "오늘 내내 생각했다며 상대를 좋아한다고 고백한다."
            return "할 말이 있다고 하다가 망설이며 쉽게 꺼내지 못한다. 좋아한다는 말은 아직 하지 않는다."
        if plot == "confession" and clock >= 19 * 60 + 35:
            return "조금 진지해지며 오늘 같이 있어서 좋았다고 조심스럽게 말한다. 좋아한다는 말은 아직 하지 않는다."
        if state.coat == "given" and state.turn % 2 == 0:
            return "겉옷을 받아 따뜻하다고 고마워한다."
        if plot == "ordinary" and clock >= 19 * 60 + 40:
            return "저기 파는 간식 이야기나 내일 일정 같은 가벼운 이야기를 한다."
        return "해 지는 풍경 이야기를 하며 저쪽으로 걷자고 한다."
    if state.scene == "불꽃놀이":
        return "불꽃이 예쁘다고 하며 방금 상대의 말에 이어서 답한다."
    if state.scene == "귀가":
        return "조금 더 걷고 싶다고 답한다."
    return "자연스럽게 대화를 이어 간다."


class UserSimulator:
    def __init__(self, llm, plot: str, silent_noon: bool) -> None:
        self.llm = llm
        self.plot = plot
        self.silent_noon = silent_noon
        self.system = load_yaml("user_sim", "v1.yaml")["system"]
        self.transcript: list[str] = []

    def next(self, engine: Engine, last_reply: str) -> str | None:
        self.transcript.append(f"상대: {last_reply}")
        if len([t for t in self.transcript if t.startswith("나:")]) == 0:
            self.transcript.append(f"나: {THIN_LINE}")  # 설계서가 정한 장치: 첫 대답은 고정한다
            return THIN_LINE
        direction = beat(engine.state, self.plot, self.silent_noon)
        if direction is None:
            self.transcript.append("나: (아무 말 없이 곁에 있음)")
            return None
        prompt = "\n".join(self.transcript[-8:]) + f"\n\n[이번 턴 방향] {direction}"
        said = self.llm.text(self.system, [{"role": "user", "text": prompt}], label="user_sim")
        said = re.sub(r"^[\"'“”]+|[\"'“”]+$", "", said.split("\n")[0]).strip()
        self.transcript.append(f"나: {said}")
        return said


def run_day(persona: str, plot: str, out: Path, *, model: str, silent_noon: bool, human: bool) -> dict:
    llm = GeminiLLM(model)
    log = EventLog(out)
    log.emit("run_meta", 0, persona=persona, plot=plot, model=model, character_prompt=DEFAULT_CHARACTER,
             analyst_prompt=DEFAULT_ANALYST, user_source="human" if human else "simulator", silent_noon=silent_noon)
    engine = Engine(llm, persona, log=log)
    log.emit("persona", 0, name=engine.persona["name"], label=engine.persona["label"], text=engine.persona["text"])
    sim = None if human else UserSimulator(GeminiLLM(model), plot, silent_noon)

    reply = engine.step(None)
    print(f"[{w.hhmm(engine.state.clock)} {engine.state.scene}] {engine.persona['name']}: {reply}")
    for _ in range(MAX_TURNS):
        if engine.state.ended:
            break
        if human:
            said = input("나 (빈 줄이면 침묵) > ").strip() or None
        else:
            said = sim.next(engine, reply)
        print(f"  유저: {said if said else '(침묵)'}")
        reply = engine.step(said)
        print(f"[{w.hhmm(engine.state.clock)} {engine.state.scene}] {engine.persona['name']}: {reply}")
    log.emit("run_end", engine.state.turn, ended=engine.state.ended, usage={
        "input": llm.usage.input_tokens, "output": llm.usage.output_tokens,
        "cached": llm.usage.cached_tokens, "calls": llm.usage.calls})
    return {"turns": engine.state.turn, "ended": engine.state.ended}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", default="warm", choices=["warm", "tsundere"])
    parser.add_argument("--plot", default="confession", choices=["confession", "ordinary"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--silent-noon", action="store_true", help="12시 직전에 유저가 침묵한다")
    parser.add_argument("--human", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()
    out = Path(args.out) if args.out else ROOT / "runs" / f"{args.persona}_{args.plot}.jsonl"
    print(run_day(args.persona, args.plot, out, model=args.model, silent_noon=args.silent_noon, human=args.human))


if __name__ == "__main__":
    main()
