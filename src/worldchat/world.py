"""월드 모델: 시간·상태·장면 판정. LLM을 호출하지 않는 순수 코드다.

설계서의 작은 온톨로지(개념 → 값 → 규칙 → 행동)를 그대로 옮겼다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

ACTION_MINUTES = {"short_talk": 5, "ride_or_sight": 30, "move": 20, "meal": 40}
ACTIONS = tuple(ACTION_MINUTES)
WARMTH_VALUES = ("얇음", "보통", "두꺼움")

DAY_START = 10 * 60
DAY_END = 22 * 60
MEET_END = 10 * 60 + 10
LUNCH_AT = 12 * 60
HUNGER_AFTER_MEAL = 8 * 60  # 데모에서 저녁 식사 제안이 불꽃놀이와 겹치지 않게 한다
MEAL_RESUGGEST_AFTER = 30
AFTERNOON_SKIP_TO = 17 * 60  # 점심 후 오후를 건너뛰어 하루를 30~40턴에 맞춘다
EVENING_AT = 19 * 60
FIREWORKS_AT = 20 * 60
FIREWORKS_DEADLINE = FIREWORKS_AT + 30
RESTAURANT_TURNS_BEFORE_SAUCE = 2
TURNS_AFTER_FIREWORKS = 2


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hhmm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def temperature_at(clock: int) -> str:
    return "선선함" if clock >= EVENING_AT else "따뜻함"


def felt_of(temperature: str, warmth: str | None, has_coat: bool) -> str:
    """기온 × 옷 보온성. 보온성을 모르면 추측하지 않고 쾌적으로 둔다."""
    if has_coat or warmth is None or warmth == "두꺼움":
        return "쾌적"
    if temperature == "추움":
        return "추움"
    if temperature == "선선함" and warmth == "얇음":
        return "추움"
    return "쾌적"


@dataclass
class WorldState:
    clock: int = DAY_START
    scene: str = "만남"
    episode: int = 0
    turn: int = 0
    temperature: str = "따뜻함"
    user_warmth: str | None = None
    felt: str = "쾌적"
    hunger: str = "없음"
    last_meal: int | None = None
    meal_proposed_at: int | None = None
    meal_agreed: bool = False
    meal_started: bool = False
    restaurant_turns: int = 0
    sauce: str = "none"  # none → armed → done
    post_sauce_turns: int = 0
    afternoon_noted: bool = False
    coat: str = "none"  # none → offered → given | declined
    fireworks: str = "pending"  # pending → deferred → fired
    deferred_turns: int = 0
    turns_since_fired: int = 0
    farewell: str = "none"  # none → asked → answered
    farewell_turns: int = 0
    ended: bool = False

    def snapshot(self) -> dict:
        data = asdict(self)
        data["clock_text"] = hhmm(self.clock)
        return data


@dataclass
class TurnPlan:
    """이번 턴에 코드가 내린 판정. 프롬프트에는 directive와 recall만 들어간다."""

    changes: list[dict]
    directive: str | None = None
    recall: list[str] | None = None
    scene_changed: bool = False
    previous_scene: str | None = None


def _set(state: WorldState, changes: list[dict], field: str, value) -> None:
    old = getattr(state, field)
    if old != value:
        setattr(state, field, value)
        changes.append({"field": field, "from": old, "to": value})


def _enter_scene(state: WorldState, plan: TurnPlan, scene: str) -> None:
    plan.previous_scene = state.scene
    plan.scene_changed = True
    _set(state, plan.changes, "scene", scene)
    state.episode += 1


def begin_turn(state: WorldState) -> TurnPlan:
    """유저 말이 들어온 직후 호출한다. 불꽃놀이 타이밍만 drama manager가 따로 정한다."""
    plan = TurnPlan(changes=[])
    state.turn += 1
    _update_scene(state, plan)
    _set(state, plan.changes, "temperature", temperature_at(state.clock))
    _update_hunger(state, plan)

    previous_felt = state.felt
    has_coat = state.coat == "given"
    _set(state, plan.changes, "felt", felt_of(state.temperature, state.user_warmth, has_coat))

    plan.directive, plan.recall = _pick_directive(state, plan, previous_felt)
    return plan


def _update_scene(state: WorldState, plan: TurnPlan) -> None:
    if state.scene == "만남" and state.clock >= MEET_END:
        _enter_scene(state, plan, "놀이동산")
    elif state.scene == "놀이동산" and state.meal_agreed and not state.meal_started:
        _enter_scene(state, plan, "식당")
    elif state.scene == "식당" and state.sauce == "done" and state.post_sauce_turns >= 1:
        _enter_scene(state, plan, "놀이동산")
        if state.clock < AFTERNOON_SKIP_TO:
            _set(state, plan.changes, "clock", AFTERNOON_SKIP_TO)
    elif state.scene == "놀이동산" and state.clock >= EVENING_AT:
        _enter_scene(state, plan, "산책")
    elif state.scene == "불꽃놀이" and state.turns_since_fired >= TURNS_AFTER_FIREWORKS:
        _enter_scene(state, plan, "귀가")

    if state.scene == "식당":
        state.restaurant_turns += 1
    if state.sauce == "done" and state.scene == "식당":
        state.post_sauce_turns += 1


def _update_hunger(state: WorldState, plan: TurnPlan) -> None:
    due = (
        state.clock >= LUNCH_AT
        if state.last_meal is None
        else state.clock - state.last_meal >= HUNGER_AFTER_MEAL
    )
    _set(state, plan.changes, "hunger", "배고픔" if due else "없음")


def _pick_directive(state: WorldState, plan: TurnPlan, previous_felt: str):
    """이번 턴의 행동 지시를 하나만 고른다. 우선순위가 높은 순서대로 검사한다."""
    if state.scene == "귀가" and state.farewell == "none":
        state.farewell = "asked"
        return "ask_farewell", ["episodes"]

    if previous_felt != "추움" and state.felt == "추움" and state.coat == "none":
        state.coat = "offered"
        return "offer_coat", ["user_clothing_warmth"]

    if state.scene == "식당":
        if state.sauce == "armed":
            state.sauce = "done"
            return "sauce_resolve", None
        if state.sauce == "none" and state.restaurant_turns >= RESTAURANT_TURNS_BEFORE_SAUCE:
            if not state.meal_started:
                _start_meal(state, plan)
            state.sauce = "armed"
            return "sauce_notice", None
        return None, None

    if state.scene == "놀이동산" and state.episode >= 2 and not state.afternoon_noted:
        state.afternoon_noted = True
        return "afternoon_passed", None

    if state.hunger == "배고픔" and not state.meal_agreed:
        proposed = state.meal_proposed_at
        if proposed is None or state.clock - proposed >= MEAL_RESUGGEST_AFTER:
            state.meal_proposed_at = state.clock
            return "suggest_meal", None

    return None, None


def _start_meal(state: WorldState, plan: TurnPlan) -> None:
    """식사 행동이 분류되지 않아도 장면은 진행되어야 하므로 코드가 식사를 시작한다."""
    state.meal_started = True
    _set(state, plan.changes, "clock", state.clock + ACTION_MINUTES["meal"])
    state.last_meal = state.clock
    _set(state, plan.changes, "hunger", "없음")


def end_turn(state: WorldState, action_type: str, events: list[str]) -> list[dict]:
    """턴 끝 처리: LLM이 고른 행동 유형에 정해진 시간을 코드가 더한다."""
    changes: list[dict] = []
    if action_type not in ACTION_MINUTES:
        action_type = "short_talk"

    if state.fireworks == "fired":
        state.turns_since_fired += 1

    already_ate = action_type == "meal" and state.meal_started
    if action_type == "meal" and not already_ate:
        state.meal_started = True
        state.last_meal = state.clock
        _set(state, changes, "hunger", "없음")
    if not already_ate:
        new_clock = min(state.clock + ACTION_MINUTES[action_type], DAY_END)
        _set(state, changes, "clock", new_clock)

    if "meal_agreed" in events and state.hunger == "배고픔":
        state.meal_agreed = True
    if "coat_given" in events and state.coat == "offered":
        _set(state, changes, "coat", "given")
        _set(state, changes, "felt", "쾌적")
    if "coat_declined" in events and state.coat == "offered":
        _set(state, changes, "coat", "declined")
    if state.farewell == "asked":
        state.farewell_turns += 1
        # 분석이 답을 놓쳐도 하루는 끝나야 하므로, 물은 뒤 한 턴이 지나면 닫는다
        if {"home_chosen", "stay_chosen"} & set(events) or state.farewell_turns >= 2:
            state.farewell = "answered"
            state.ended = True
    return changes


def fire_fireworks(state: WorldState, plan: TurnPlan) -> None:
    _set(state, plan.changes, "fireworks", "fired")
    if state.scene != "불꽃놀이":
        _enter_scene(state, plan, "불꽃놀이")
    plan.directive = "fireworks_fire"
    plan.recall = ["episodes"]
