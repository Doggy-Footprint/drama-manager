"""한 턴의 파이프라인.

유저 메시지 → 월드 모델 → drama manager → 기억 검색 → 프롬프트 조립 → 캐릭터 LLM
→ 턴 분석(행동 유형·사실 추출) → 시간 더하기 → 이벤트 로그

prepare / generate / analyze / finish로 나눈 이유: golden 평가가 각 단계 사이에
정답을 끼워 넣어(teacher forcing) 한 턴씩 독립적으로 채점하기 위해서다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import drama as drama_mod
from . import world as w
from .drama import DramaDecision, DramaJudgement
from .llm import LLM
from .memory import Memory

PROMPTS = Path(__file__).parent / "prompts"
DEFAULT_CHARACTER = "v4"
DEFAULT_ANALYST = "v4"
IDLE_TEXT = "(상대는 말없이 곁에 있다)"
SCENE_START_TEXT = "(장면 시작)"

ANALYSIS_EVENTS = [
    "meal_agreed", "meal_declined", "sauce_wiped", "sauce_declined",
    "coat_given", "coat_declined", "home_chosen", "stay_chosen",
]
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": list(w.ACTIONS)},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": ["user_clothing_warmth"]},
                    "value": {"type": "string", "enum": list(w.WARMTH_VALUES)},
                    "quote": {"type": "string"},
                },
                "required": ["key", "value", "quote"],
            },
        },
        "events": {"type": "array", "items": {"type": "string", "enum": ANALYSIS_EVENTS}},
    },
    "required": ["action_type", "facts", "events"],
}
DRAMA_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "boolean"} for k in ("emotional", "relationship", "tension", "peak")},
    "required": ["emotional", "relationship", "tension", "peak"],
}


def load_yaml(*parts: str) -> dict:
    return yaml.safe_load((PROMPTS.joinpath(*parts)).read_text(encoding="utf-8"))


def load_persona(name: str) -> dict:
    return load_yaml("persona", f"{name}.yaml")


class EventLog:
    """모든 판정과 상태 변화를 남긴다. 패널·평가·2D는 이 로그만 읽는다."""

    def __init__(self, path: Path | None = None) -> None:
        self.events: list[dict] = []
        self.path = path
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def emit(self, kind: str, turn: int, **data) -> None:
        import json

        event = {"kind": kind, "turn": turn, **data}
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")


@dataclass
class TurnContext:
    user_text: str | None
    plan: w.TurnPlan
    recalled: list[dict]
    system: str
    messages: list[dict]
    situation: str
    is_opening: bool
    drama_decision: DramaDecision | None = None


class Engine:
    def __init__(
        self,
        llm: LLM,
        persona: str = "warm",
        *,
        character_version: str = DEFAULT_CHARACTER,
        analyst_version: str = DEFAULT_ANALYST,
        log: EventLog | None = None,
    ) -> None:
        self.llm = llm
        self.persona = load_persona(persona)
        self.character = load_yaml("character", f"{character_version}.yaml")
        self.analyst = load_yaml("analyst", f"{analyst_version}.yaml")
        self.state = w.WorldState()
        self.memory = Memory()
        self.history: list[dict] = []
        self.log = log or EventLog()
        self.episode_start = self.state.clock

    # ── 1단계: 판정과 프롬프트 조립 ─────────────────────────────
    def prepare(
        self,
        user_text: str | None,
        *,
        drama: DramaJudgement | None = None,
        summary: str | None = None,
    ) -> TurnContext:
        state = self.state
        is_opening = state.turn == 0 and user_text is None
        self.log.emit("turn_start", state.turn + 1, user=user_text, idle=user_text is None and not is_opening,
                      clock=state.clock)
        if is_opening:
            state.turn = 1
            plan = w.TurnPlan(changes=[], directive="open_greeting")
        else:
            plan = w.begin_turn(state)

        decision = self._run_drama(plan, user_text, drama)

        if plan.scene_changed:
            self._close_episode(plan, summary)
        for change in plan.changes:
            self.log.emit("state_changed", state.turn, **change)
        if plan.directive:
            self.log.emit("directive", state.turn, name=plan.directive, text=self.character["directives"][plan.directive])

        recalled = self.memory.retrieve(plan.recall)
        if recalled:
            self.log.emit("memory_retrieved", state.turn, trigger=plan.directive, items=recalled)

        situation = self._situation(plan, recalled)
        system = self._system()
        messages = self._messages(user_text, situation)
        self.log.emit("prompt_assembled", state.turn, situation=situation)
        return TurnContext(user_text, plan, recalled, system, messages, situation, is_opening, decision)

    def _run_drama(self, plan, user_text, judgement) -> DramaDecision | None:
        state = self.state
        if not drama_mod.in_window(state):
            return None
        if judgement is None:
            judgement = self.judge_drama(user_text)
        decision = drama_mod.decide(state, judgement)
        self.log.emit("drama_decision", state.turn, action=decision.action, reason=decision.reason,
                      judgement=vars(judgement), clock=state.clock)
        if decision.action == "fire":
            w.fire_fireworks(state, plan)
        else:
            state.fireworks = "deferred"
            state.deferred_turns += 1
        return decision

    def judge_drama(self, user_text: str | None) -> DramaJudgement:
        recent = self.history[-6:] + [{"role": "user", "text": user_text or IDLE_TEXT}]
        transcript = "\n".join(f"{'유저' if m['role'] == 'user' else '캐릭터'}: {m['text']}" for m in recent)
        result = self.llm.json(self.analyst["drama_system"], [{"role": "user", "text": transcript}],
                               DRAMA_SCHEMA, label="drama")
        return DramaJudgement(**{k: bool(result.get(k)) for k in ("emotional", "relationship", "tension", "peak")})

    def _close_episode(self, plan, summary: str | None) -> None:
        state = self.state
        text = summary if summary is not None else self._summarize()
        episode = self.memory.add_episode(state.episode - 1, plan.previous_scene, self.episode_start, state.clock, text)
        self.log.emit("episode_closed", state.turn, scene=episode.scene, summary=text,
                      start=episode.start_clock, end=episode.end_clock, next_scene=state.scene)
        self.history = self.history[-2:]  # 말이 끊기지 않도록 직전 한 번의 주고받음만 잇는다
        self.episode_start = state.clock

    def _summarize(self) -> str:
        transcript = "\n".join(f"{'유저' if m['role'] == 'user' else '캐릭터'}: {m['text']}" for m in self.history)
        if not transcript:
            return "(대화 없음)"
        return self.llm.text(self.analyst["summary_system"], [{"role": "user", "text": transcript}], label="summary")

    def _system(self) -> str:
        parts = [self.persona["text"].strip(), self.character["rules"].strip()]
        long_term = self.memory.long_term_text()
        if long_term:
            parts.append("[오늘 지금까지 있었던 일]\n" + long_term)
        return "\n\n".join(parts)

    def _situation(self, plan: w.TurnPlan, recalled: list[dict]) -> str:
        state = self.state
        lines = [self.character["situation_header"].strip(),
                 f"시각: {w.hhmm(state.clock)} / 장면: {state.scene}"]
        judged = []
        if state.hunger == "배고픔" and plan.directive == "suggest_meal":
            judged.append("배고픔")
        if state.felt == "추움" and plan.directive == "offer_coat":
            judged.append("상대의 체감이 추움으로 바뀜")
        if judged:
            lines.append("판정: " + ", ".join(judged))
        if plan.directive:
            lines.append("지금 할 일: " + self.character["directives"][plan.directive].strip())
        if recalled:
            lines.append("떠오른 기억:\n" + "\n".join(f"- {item['text']}" for item in recalled))
        return "\n".join(lines)

    def _messages(self, user_text: str | None, situation: str) -> list[dict]:
        said = user_text if user_text is not None else IDLE_TEXT
        messages = list(self.history)
        if messages and messages[0]["role"] == "assistant":
            messages.insert(0, {"role": "user", "text": SCENE_START_TEXT})
        messages.append({"role": "user", "text": f"{said}\n\n{situation}"})
        return messages

    # ── 2단계: 캐릭터 응답 ─────────────────────────────────────
    def generate(self, ctx: TurnContext) -> str:
        reply = self.llm.text(ctx.system, ctx.messages, label="character")
        return re.sub(r"\s*\n+\s*", " ", reply).strip()

    # ── 3단계: 턴 분석 ─────────────────────────────────────────
    def analyze(self, ctx: TurnContext, reply: str) -> dict:
        said = ctx.user_text if ctx.user_text is not None else IDLE_TEXT
        text = f"장면: {self.state.scene}\n유저: {said}\n캐릭터: {reply}"
        return self.llm.json(self.analyst["analysis_system"], [{"role": "user", "text": text}],
                             ANALYSIS_SCHEMA, label="analysis")

    # ── 4단계: 턴 마감 ─────────────────────────────────────────
    def finish(self, ctx: TurnContext, reply: str, analysis: dict) -> None:
        state = self.state
        self.log.emit("character_reply", state.turn, text=reply)
        self.log.emit("analysis", state.turn, **analysis)
        for candidate in analysis.get("facts", []):
            fact, rejected = self.memory.store_fact(candidate, ctx.user_text, clock=state.clock,
                                                    scene=state.scene, turn=state.turn)
            if fact:
                self.log.emit("memory_stored", state.turn, key=fact.key, value=fact.value, quote=fact.quote,
                              clock=fact.clock, scene=fact.scene, replaced=fact.replaced)
                if fact.key == "user_clothing_warmth":
                    state.user_warmth = fact.value
            else:
                self.log.emit("memory_rejected", state.turn, candidate=candidate, reason=rejected)

        if ctx.user_text is not None:
            self.history.append({"role": "user", "text": ctx.user_text})
        self.history.append({"role": "assistant", "text": reply})

        if not ctx.is_opening:
            before = state.clock
            changes = w.end_turn(state, analysis.get("action_type", "short_talk"), analysis.get("events", []))
            for change in changes:
                self.log.emit("state_changed", state.turn, **change)
            self.log.emit("clock_advanced", state.turn, action=analysis.get("action_type"),
                          minutes=state.clock - before, clock=state.clock)
        self.log.emit("turn_end", state.turn, world=state.snapshot(), memory=self.memory.snapshot())

    def step(self, user_text: str | None) -> str:
        ctx = self.prepare(user_text)
        reply = self.generate(ctx)
        self.finish(ctx, reply, self.analyze(ctx, reply))
        return reply
