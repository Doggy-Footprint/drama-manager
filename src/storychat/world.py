"""월드 모델: 시간과 몸 상태, 시간표에 묶인 사건. LLM을 부르지 않는다."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .schema import FRAME_AXES, Checkpoint, hhmm, parse_hhmm

ACTION_MINUTES = {"short_talk": 5, "activity": 30, "move": 15, "meal": 40}
DAY_START = parse_hhmm("17:30")
DAY_END = parse_hhmm("23:00")
HUNGER_AFTER_MEAL = 6 * 60

# 프레임이 바뀌어도 유지되는 세계의 체크포인트. 사실만 주입하고 행동은 지시하지 않는다.
WORLD_CHECKPOINTS = [
    Checkpoint("hunger", {"state_eq": {"hunger": "배고픔"}}, "배가 슬슬 고파졌다.", source="world", bound_to="world"),
    Checkpoint("evening_cold", {"clock_gte": "19:00"}, "해가 지면서 바람이 제법 차가워졌다.", source="world", bound_to="world"),
    Checkpoint("fireworks", {"clock_gte": "20:00"}, "놀이동산 하늘에 불꽃이 터지기 시작했다.",
               source="world", bound_to="world", deferrable=True, deadline="20:30"),
]


@dataclass
class WorldState:
    clock: int = DAY_START
    place: str = "놀이동산 정문"
    relationship: str = "7개월차 연인"
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
