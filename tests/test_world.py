"""LLM 없이 도는 단독 테스트. 월드 모델·메모리·drama manager의 판정을 검증한다."""
import pytest

from worldchat import drama as d
from worldchat import world as w
from worldchat.engine import Engine
from worldchat.llm import ScriptedLLM
from worldchat.memory import Memory


@pytest.mark.parametrize(
    "temperature,warmth,coat,expected",
    [
        ("따뜻함", "얇음", False, "쾌적"),  # 설계서의 핵심 예: 따뜻한 날 + 얇은 옷 → 겉옷 권유 금지
        ("선선함", "얇음", False, "추움"),
        ("선선함", "보통", False, "쾌적"),
        ("선선함", "두꺼움", False, "쾌적"),
        ("선선함", None, False, "쾌적"),  # 모르면 추측하지 않는다
        ("선선함", "얇음", True, "쾌적"),  # 겉옷을 받으면 풀린다
        ("추움", "보통", False, "추움"),
    ],
)
def test_felt_rule(temperature, warmth, coat, expected):
    assert w.felt_of(temperature, warmth, coat) == expected


def test_time_is_added_by_code_per_action_type():
    state = w.WorldState()
    for action, minutes in w.ACTION_MINUTES.items():
        before = state.clock
        w.end_turn(state, action, [])
        assert state.clock - before == minutes


def test_unknown_action_falls_back_to_short_talk():
    state = w.WorldState()
    w.end_turn(state, "teleport", [])
    assert state.clock == w.DAY_START + 5


def test_hunger_turns_on_at_noon_and_suggests_once_until_resuggest_window():
    state = w.WorldState(clock=w.LUNCH_AT, scene="놀이동산", episode=1)
    assert w.begin_turn(state).directive == "suggest_meal"
    assert w.begin_turn(state).directive is None  # 바로 다시 조르지 않는다
    state.clock += w.MEAL_RESUGGEST_AFTER
    assert w.begin_turn(state).directive == "suggest_meal"


def test_coat_only_fires_on_transition_to_cold():
    state = w.WorldState(clock=w.EVENING_AT, scene="놀이동산", episode=3, afternoon_noted=True,
                         user_warmth="얇음", last_meal=750, meal_agreed=True, meal_started=True, sauce="done")
    plan = w.begin_turn(state)
    assert plan.directive == "offer_coat"
    assert plan.recall == ["user_clothing_warmth"]
    assert state.scene == "산책"
    assert w.begin_turn(state).directive is None  # 겉옷은 한 번만 건넨다


def test_sauce_event_leaves_a_turn_for_the_user():
    state = w.WorldState(clock=750, scene="식당", episode=2, meal_agreed=True, restaurant_turns=1, hunger="배고픔")
    assert w.begin_turn(state).directive == "sauce_notice"
    assert w.begin_turn(state).directive == "sauce_resolve"
    assert state.sauce == "done"


def test_user_fact_must_be_quoted_from_user_text():
    memory = Memory()
    fact, _ = memory.store_fact({"key": "user_clothing_warmth", "value": "얇음", "quote": "좀 얇나"},
                                "봄이라 산뜻하게. 좀 얇나?", clock=600, scene="만남", turn=1)
    assert fact.value == "얇음"
    # 캐릭터가 지어낸 말은 유저 발화에 없으므로 저장되지 않는다
    fact, reason = memory.store_fact({"key": "user_clothing_warmth", "value": "두꺼움", "quote": "너 두껍게 입었잖아"},
                                     "오늘 재밌다", clock=700, scene="놀이동산", turn=4)
    assert fact is None and "인용" in reason
    assert memory.facts["user_clothing_warmth"].value == "얇음"


def test_user_change_replaces_value_and_keeps_previous():
    memory = Memory()
    memory.store_fact({"key": "user_clothing_warmth", "value": "얇음", "quote": "얇나"}, "좀 얇나?",
                      clock=600, scene="만남", turn=1)
    fact, _ = memory.store_fact({"key": "user_clothing_warmth", "value": "두꺼움", "quote": "후드티"}, "두꺼운 후드티 샀어",
                                clock=900, scene="놀이동산", turn=9)
    assert fact.replaced == "얇음"


def test_ontology_rejects_unknown_value():
    fact, reason = Memory().store_fact({"key": "user_clothing_warmth", "value": "엄청 얇음", "quote": "얇"}, "얇",
                                       clock=600, scene="만남", turn=1)
    assert fact is None and "온톨로지" in reason


@pytest.mark.parametrize(
    "clock,judgement,action",
    [
        (w.FIREWORKS_AT, d.DramaJudgement(), "fire"),  # 평범하면 제시간
        (w.FIREWORKS_AT, d.DramaJudgement(tension=True), "defer"),  # 중요하면 미룸
        (w.FIREWORKS_AT + 10, d.DramaJudgement(relationship=True, peak=True), "fire"),  # 정점이면 터짐
        (w.FIREWORKS_DEADLINE, d.DramaJudgement(tension=True), "fire"),  # 한도에 닿으면 그냥 터짐
    ],
)
def test_drama_decision(clock, judgement, action):
    state = w.WorldState(clock=clock, scene="산책")
    assert d.decide(state, judgement).action == action


def test_farewell_closes_even_if_analysis_misses_the_answer():
    state = w.WorldState(scene="귀가", farewell="asked")
    w.end_turn(state, "short_talk", [])
    assert not state.ended
    w.end_turn(state, "short_talk", [])
    assert state.ended


def test_morning_memory_not_in_prompt_until_judged_cold():
    """핵심 인과: 아침 기억은 낮 프롬프트에 없고, 추움 판정 턴에만 들어간다."""
    llm = ScriptedLLM({})
    engine = Engine(llm, "warm", character_version="v4", analyst_version="v4")
    engine.memory.store_fact({"key": "user_clothing_warmth", "value": "얇음", "quote": "좀 얇나"}, "좀 얇나?",
                             clock=600, scene="만남", turn=1)
    state = engine.state
    state.user_warmth = "얇음"
    state.turn, state.scene, state.episode, state.afternoon_noted = 5, "놀이동산", 3, True
    state.meal_agreed, state.meal_started, state.sauce, state.last_meal = True, True, "done", 750

    state.clock = 15 * 60
    day = engine.prepare("재밌다")
    assert "좀 얇나" not in day.situation and "좀 얇나" not in day.system
    engine.finish(day, "그치", {"action_type": "short_talk", "facts": [], "events": []})

    state.clock = w.EVENING_AT
    evening = engine.prepare("해 진다", summary="오후를 보냈다")
    assert evening.plan.directive == "offer_coat"
    assert "좀 얇나" in evening.situation


def test_episode_cut_removes_morning_lines_from_history():
    llm = ScriptedLLM({"summary": ["만나서 인사했다."]})
    engine = Engine(llm, "warm", character_version="v4", analyst_version="v4")
    for line in ("좀 얇나?", "응 가자", "저기 보여"):
        ctx = engine.prepare(line)
        engine.finish(ctx, "그래", {"action_type": "short_talk", "facts": [], "events": []})
    assert engine.state.scene == "놀이동산"
    assert all("좀 얇나" not in m["text"] for m in engine.history)
    assert "만나서 인사했다." in engine._system()
