"""월드 모델: 시간과 몸 상태. LLM을 부르지 않는다. 사건은 장면 파일에 있다."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .schema import FRAME_AXES, hhmm, parse_hhmm

ACTION_MINUTES = {"short_talk": 5, "activity": 30, "move": 15, "meal": 40}
DAY_START = parse_hhmm("17:30")
DAY_END = parse_hhmm("23:00")
HUNGER_AFTER_MEAL = 6 * 60


@dataclass
class WorldState:
    clock: int = DAY_START
    place: str = "놀이동산 정문"
    relationship: str = "두 달째 썸"
    temperature: str = "선선함"
    hunger: str = "없음"
    last_meal: int = parse_hhmm("12:00")
    turn: int = 0
    ended: bool = False

    @property
    def frame(self) -> dict:
        return {axis.name: getattr(self, axis.name) for axis in FRAME_AXES}

    def view(self) -> dict:
        return {"place": self.place, "relationship": self.relationship,
                "hunger": self.hunger, "temperature": self.temperature}

    def snapshot(self) -> dict:
        return {**asdict(self), "clock_text": hhmm(self.clock)}


def refresh(state: WorldState) -> list[dict]:
    """시각에서 따라오는 상태를 다시 계산하고 바뀐 것을 돌려준다."""
    changes = []
    for name, value in (("temperature", "쌀쌀함" if state.clock >= parse_hhmm("19:00") else "선선함"),
                        ("hunger", "배고픔" if state.clock - state.last_meal >= HUNGER_AFTER_MEAL else "없음")):
        old = getattr(state, name)
        if old != value:
            setattr(state, name, value)
            changes.append({"field": name, "from": old, "to": value})
    return changes


def advance(state: WorldState, action: str) -> int:
    """LLM이 고른 행동 유형에 정해진 시간을 코드가 더한다."""
    minutes = ACTION_MINUTES.get(action, ACTION_MINUTES["short_talk"])
    state.clock = min(state.clock + minutes, DAY_END)
    if action == "meal":
        state.last_meal = state.clock
    return minutes
