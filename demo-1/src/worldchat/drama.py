"""Drama manager: 불꽃놀이가 터질 순간만 고른다. 판단 근거는 패널에 그대로 보인다."""
from __future__ import annotations

from dataclasses import dataclass

from .world import FIREWORKS_AT, FIREWORKS_DEADLINE, WorldState, hhmm


@dataclass
class DramaJudgement:
    emotional: bool = False
    relationship: bool = False
    tension: bool = False
    peak: bool = False

    @property
    def important(self) -> bool:
        return self.emotional or self.relationship or self.tension


@dataclass
class DramaDecision:
    action: str  # fire | defer
    reason: str


def in_window(state: WorldState) -> bool:
    return state.clock >= FIREWORKS_AT and state.fireworks != "fired"


def decide(state: WorldState, judgement: DramaJudgement) -> DramaDecision:
    if not judgement.important:
        return DramaDecision("fire", f"평범한 대화. {hhmm(state.clock)}에 그대로 터뜨림")
    if judgement.peak:
        delay = state.clock - FIREWORKS_AT
        return DramaDecision("fire", f"유저가 마음을 드러낸 직후. 예정보다 {delay}분, {state.deferred_turns}턴 미룬 뒤 터뜨림")
    if state.clock >= FIREWORKS_DEADLINE:
        return DramaDecision("fire", f"미룰 수 있는 한도({hhmm(FIREWORKS_DEADLINE)}) 도달. 그냥 터뜨림")
    return DramaDecision("defer", f"중요한 대화 감지, 불꽃놀이 지연 {state.deferred_turns + 1}턴째")
