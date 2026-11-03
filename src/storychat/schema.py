"""프레임과 trigger 어휘. LLM은 trigger를 쓰기만 하고, 평가는 여기서 코드가 한다."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field



@dataclass(frozen=True)
class Axis:
    """프레임 축 하나. 축을 더해도 엔진은 고치지 않는다. 값이 바뀌면 프레임 플랜을 새로 만든다."""
    name: str               # 월드 상태의 키
    label: str
    proposal: str           # 턴 분석에서 새 값을 제안하는 필드
    needs_branch: bool      # 분기 신호가 켜진 턴에만 바뀌는가
    resets_plan: bool       # 바뀌면 이전 플랜과 뿌리지 못한 디테일을 버리는가


FRAME_AXES = (Axis("place", "장소", "move_to", False, True),
              Axis("relationship", "관계", "relationship_shift", True, False))
STATE_KEYS = ("place", "relationship", "hunger", "temperature")
BASE_SIGNALS = ("tension", "branch")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hhmm(text: str) -> int:
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


@dataclass
class TriggerContext:
    clock: int
    turns_in_frame: int
    state: dict
    signals: set[str] = field(default_factory=set)
    fired: set[str] = field(default_factory=set)


def _valid_signal(value) -> bool:
    return isinstance(value, str) and (value in BASE_SIGNALS or (value.startswith("topic:") and len(value) > 6))


def validate_trigger(trigger, known_ids: set[str] | None = None) -> list[str]:
    """문제 목록을 돌려준다. 빈 목록이면 코드가 평가할 수 있는 trigger다."""
    if not isinstance(trigger, dict) or len(trigger) != 1:
        return [f"조건 하나짜리 객체여야 함: {trigger!r}"]
    (key, value), = trigger.items()
    if key in ("all", "any"):
        if not isinstance(value, list) or not value:
            return [f"{key}에는 조건 목록이 필요함"]
        return [problem for item in value for problem in validate_trigger(item, known_ids)]
    if key == "clock_gte":
        return [] if isinstance(value, str) and _HHMM.match(value) else [f"HH:MM이 아님: {value!r}"]
    if key == "turns_in_frame_gte":
        return [] if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else [f"정수가 아님: {value!r}"]
    if key == "state_eq":
        if not isinstance(value, dict) or not value or set(value) - set(STATE_KEYS):
            return [f"허용되지 않는 상태 키: {value!r}"]
        return []
    if key == "signal":
        return [] if _valid_signal(value) else [f"모르는 신호: {value!r}"]
    if key == "after":
        if not isinstance(value, str):
            return [f"체크포인트 id가 아님: {value!r}"]
        return [] if known_ids is None or value in known_ids else [f"없는 체크포인트: {value!r}"]
    return [f"어휘에 없는 조건: {key}"]


def evaluate(trigger: dict, ctx: TriggerContext) -> bool:
    (key, value), = trigger.items()
    if key == "all":
        return all(evaluate(item, ctx) for item in value)
    if key == "any":
        return any(evaluate(item, ctx) for item in value)
    if key == "clock_gte":
        return ctx.clock >= parse_hhmm(value)
    if key == "turns_in_frame_gte":
        return ctx.turns_in_frame >= value
    if key == "state_eq":
        return all(ctx.state.get(k) == v for k, v in value.items())
    if key == "signal":
        return value in ctx.signals
    if key == "after":
        return value in ctx.fired
    raise ValueError(f"검증되지 않은 trigger: {trigger!r}")


def topics_in(trigger) -> set[str]:
    """trigger가 기다리는 화제 이름. 드라마 매니저에게 고를 수 있는 화제 목록으로 준다."""
    if not isinstance(trigger, dict):
        return set()
    found: set[str] = set()
    for key, value in trigger.items():
        if key in ("all", "any") and isinstance(value, list):
            for item in value:
                found |= topics_in(item)
        elif key == "signal" and isinstance(value, str) and value.startswith("topic:"):
            found.add(value[6:])
    return found


@dataclass
class Checkpoint:
    id: str
    trigger: dict
    inject: str
    source: str = "plan"  # world | plan
    bound_to: str = "place"
    deferrable: bool = False
    deadline: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)
