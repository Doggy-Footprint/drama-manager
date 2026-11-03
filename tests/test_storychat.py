"""LLM 없는 판정 테스트. 캐릭터의 행동이 아니라 코드가 정하는 것만 검사한다."""
import pytest

from storychat.engine import Engine
from storychat.llm import RefusalError, ScriptedLLM
from storychat.memory import KEEP_RAW_TURNS, DetailLedger, quoted
from storychat.schema import TriggerContext, evaluate, parse_hhmm, topics_in, validate_trigger

JUDGE = {"tension": False, "branch": None, "topics": [], "recall_big": []}
TENSE = {**JUDGE, "tension": True}
ANALYSIS = {"action_type": "short_talk", "move_to": None, "relationship_shift": None, "parted": False,
            "shown_said_quote": None, "payoff_used_quote": None, "new_details": []}
PLAN = {"checkpoints": [], "details": [{"text": "옆 공연장에 사람이 많다", "affordances": ["인파로 헤어짐"]}]}


def ctx(**kw):
    base = dict(clock=parse_hhmm("19:00"), turns_in_frame=3, state={"hunger": "배고픔"}, signals={"tension", "topic:영화"}, fired={"a"})
    return TriggerContext(**{**base, **kw})


@pytest.mark.parametrize("trigger", [
    {"clock_gte": "20:00"}, {"turns_in_frame_gte": 2}, {"state_eq": {"hunger": "배고픔"}}, {"signal": "tension"},
    {"signal": "topic:영화"}, {"after": "a"}, {"all": [{"signal": "branch"}, {"any": [{"clock_gte": "19:00"}, {"after": "a"}]}]},
])
def test_vocabulary_accepted(trigger):
    assert validate_trigger(trigger, {"a"}) == []


@pytest.mark.parametrize("trigger", [
    "둘 사이가 어색해지면", {"mood": "어색함"}, {"clock_gte": "저녁"}, {"clock_gte": "25:00"}, {"signal": "romantic"},
    {"state_eq": {"mood": "좋음"}}, {"after": "없는것"}, {"all": []}, {"all": [{"mood": 1}]}, {"clock_gte": "19:00", "signal": "tension"},
    {"turns_in_frame_gte": True},
])
def test_vocabulary_rejected(trigger):
    assert validate_trigger(trigger, {"a"}) != []


def test_evaluate():
    assert evaluate({"clock_gte": "19:00"}, ctx())
    assert not evaluate({"clock_gte": "19:01"}, ctx())
    assert evaluate({"all": [{"signal": "tension"}, {"signal": "topic:영화"}]}, ctx())
    assert not evaluate({"all": [{"signal": "tension"}, {"signal": "topic:회사 일"}]}, ctx())
    assert evaluate({"any": [{"after": "b"}, {"after": "a"}]}, ctx())
    assert topics_in({"any": [{"signal": "topic:x"}, {"all": [{"signal": "tension"}, {"signal": "topic:y"}]}]}) == {"x", "y"}


def test_quote_ignores_spacing_only():
    assert quoted("사람들 많다", "공연하나봐. 사람들  많다!")
    assert not quoted("사람 없다", "사람들 많다")
    assert not quoted("", "무엇이든")


def make(responses=None, card="warm"):
    llm = ScriptedLLM({"plan": [PLAN], **(responses or {})})
    llm.responses.setdefault("analysis", [])
    return Engine(llm, card), llm


def prompts(llm, label="character"):
    return [c["system"] + c["prompt"] for c in llm.calls if c["label"] == label]


def scripted(n, **extra):
    return {"judge": [JUDGE] * n, "analysis": [ANALYSIS] * n, **extra}


def test_hidden_not_in_prompt_until_reveal_conditions():
    engine, llm = make({"judge": [{**JUDGE, "tension": True}, {**JUDGE, "topics": ["영화"]},
                                  {**JUDGE, "tension": True, "topics": ["영화"]}, JUDGE],
                        "analysis": [ANALYSIS] * 5})
    fact = "후기를 미리 찾아 읽어"
    engine.step(None)
    engine.step("왜 그래")          # 긴장만
    engine.step("영화 볼까")        # 화제만
    before = prompts(llm)
    assert all(fact not in p for p in before)
    assert all(fact not in c["system"] + c["prompt"] for c in llm.calls)  # 드라마 매니저도 내용을 보지 않는다
    engine.step("영화 얘기만 하면 왜 그래")  # 둘 다
    after = prompts(llm)[-1]
    assert fact in after and "지금까지 숨긴 이유" in after
    engine.step("다음")
    assert fact in prompts(llm)[-1]
    assert "주말 데이트는 거의 항상 영화" in engine.system  # cover와 공개 히스토리는 처음부터 있다


def test_checkpoint_defer_then_deadline():
    engine, _ = make(scripted(3))
    engine.state.clock = parse_hhmm("20:00")
    engine.step(None)
    assert "fireworks" in engine.fired  # 평온하면 제시간

    engine, _ = make({"judge": [TENSE, TENSE, {**TENSE, "branch": "고백"}], "analysis": [ANALYSIS] * 4})
    engine.state.turn = 1
    engine.state.clock = parse_hhmm("20:05")
    engine.step("할 말 있어")
    assert "fireworks" not in engine.fired  # 긴장 중이면 미룬다
    engine.state.clock = parse_hhmm("20:30")
    engine.step("저기")
    assert "fireworks" in engine.fired  # 한도에 닿으면 터진다

    engine, _ = make({"judge": [{**TENSE, "branch": "고백"}], "analysis": [ANALYSIS]})
    engine.state.turn = 1
    engine.state.clock = parse_hhmm("20:05")
    engine.step("좋아해")
    assert "fireworks" in engine.fired  # 분기 순간에는 미루지 않는다


def test_invalid_plan_trigger_retried_and_dropped():
    bad = {"id": "x", "inject": "비", "bound_to": "place", "deferrable": False, "deadline": None, "trigger": {"mood": "슬픔"}}
    good = {**bad, "id": "y", "trigger": {"clock_gte": "19:00"}}
    llm = ScriptedLLM({"plan": [{"checkpoints": [bad, good], "details": []}, {"checkpoints": [bad, good], "details": []}]})
    engine = Engine(llm, "warm")
    assert [c.id for c in engine.plan] == ["y"]
    assert len(prompts(llm, "plan")) == 2
    assert any(e["kind"] == "plan_generated" and e["rejected"] for e in engine.log.events)


def test_seed_needs_real_quote_to_be_planted():
    said = {**ANALYSIS, "shown_said_quote": "사람들 많다"}
    fake = {**ANALYSIS, "shown_said_quote": "지어낸 인용"}
    engine, _ = make({"judge": [JUDGE], "analysis": [said], "character": ["저기 사람들 많다."]})
    engine.last_seed_turn = -5
    engine.step(None)
    assert [d.id for d in engine.ledger.planted()] == ["d1"]

    engine, _ = make({"analysis": [fake], "character": ["오늘 좋다."]})
    engine.last_seed_turn = -5
    engine.step(None)
    assert engine.ledger.planted() == [] and engine.ledger.items["d1"].status == "seed"


def test_payoff_only_from_planted_on_tension_and_unused_is_not_paid():
    used = {**ANALYSIS, "payoff_used_quote": "사람들"}
    engine, llm = make({
        "judge": [JUDGE, TENSE, TENSE, TENSE],
        "analysis": [{**ANALYSIS, "shown_said_quote": "사람들 많다"}, ANALYSIS, ANALYSIS, used],
        "character": ["저기 사람들 많다.", "응.", "왜?", "아까 그 사람들 봐."],
        "payoff": [{"detail_id": "없는것", "how": "dialogue", "fact": "x"},
                   {"detail_id": "d1", "how": "world", "fact": "공연이 끝나 인파가 몰려온다."}],
    })
    engine.last_seed_turn = -5
    engine.step(None)
    engine.step("응")   # 평온: 떡밥 판단 자체를 하지 않는다
    assert prompts(llm, "payoff") == []
    engine.step("너 왜 그래")  # 후보를 지어내면 버린다
    assert engine.ledger.items["d1"].status == "planted"
    engine.step("진짜 왜 그래")
    assert "인파가 몰려온다" in prompts(llm)[-1]
    assert engine.ledger.items["d1"].status == "paid"


def test_expired_when_place_changes_but_planted_survive():
    ledger = DetailLedger()
    a, b = ledger.add_seeds([{"text": "a"}, {"text": "b"}], "p1")
    ledger.plant(a.id, "q", 1)
    assert ledger.expire_seeds() == [b.id]
    assert ledger.items[a.id].status == "planted"


def test_place_move_regenerates_plan():
    moved = {**ANALYSIS, "move_to": "식당가"}
    second = {"checkpoints": [], "details": [{"text": "식당에 줄이 길다", "affordances": []}]}
    engine, llm = make({"plan": [PLAN, second], "analysis": [moved]})
    engine.step(None)
    assert engine.state.place == "식당가" and len(prompts(llm, "plan")) == 2
    assert engine.ledger.items["d1"].status == "expired" and engine.ledger.items["d2"].status == "seed"


def test_relationship_shift_needs_branch():
    shift = {**ANALYSIS, "relationship_shift": "결혼을 약속한 연인"}
    engine, _ = make({"judge": [JUDGE], "analysis": [ANALYSIS, shift]})
    engine.step(None)
    engine.step("안녕")
    assert engine.state.relationship == "7개월차 연인"
    engine, _ = make({"judge": [{**JUDGE, "branch": "청혼"}], "analysis": [ANALYSIS, shift]})
    engine.step(None)
    engine.step("결혼하자")
    assert engine.state.relationship == "결혼을 약속한 연인"


def test_compress_is_retrospective_and_keeps_recent_raw():
    n = KEEP_RAW_TURNS + 6
    summary = {"title": "정문에서 만남", "summary": "정문에서 만났다.", "relationship": "새 설명"}
    engine, llm = make({"judge": [JUDGE] * n, "analysis": [ANALYSIS] * n, "compress": [summary]})
    engine.step(None)
    for i in range(2, 5):
        engine.step(f"초반{i}")
    engine.boundaries.add(5)  # T5에서 사건이 일어났다고 표시
    for i in range(5, KEEP_RAW_TURNS + 4):
        engine.step(f"말{i}")
        assert not prompts(llm, "compress")  # 아직 지나간 이야기가 아니다
    engine.step("마지막")
    assert len(prompts(llm, "compress")) == 1
    assert engine.raw_start == 5 and engine.episodes.today_titles() == ["정문에서 만남"]
    assert engine.relationship.state == "새 설명"
    engine.step("다음")
    final = prompts(llm)[-1]
    assert "초반2" not in final and "말5" in final and "정문에서 만남" in final
    assert "정문에서 만났다." not in final  # 내용은 검색될 때만 들어온다


def test_alive_segment_is_postponed():
    n = KEEP_RAW_TURNS + 4
    engine, llm = make({"judge": [JUDGE] * n, "analysis": [ANALYSIS] * n, "alive": [{"alive": True}]})
    engine.boundaries.add(4)
    for i in range(KEEP_RAW_TURNS + 3):
        engine.step(None if i == 0 else f"말{i}")
    assert len(prompts(llm, "alive")) == 1 and not prompts(llm, "compress") and engine.raw_start == 1
    assert engine.compress_after > engine.state.turn


def test_recall_is_hierarchical_and_expires():
    engine, llm = make({"judge": [{**JUDGE, "recall_big": ["trip", "없는것"]}, JUDGE, JUDGE, JUDGE],
                        "drill": [{"ids": ["trip-1"]}], "analysis": [ANALYSIS] * 4})
    engine.director.drill = lambda *a: ["trip-1"]
    engine.state.turn = 1
    engine.step("강릉 기억나?")
    assert "놓친 기차" in prompts(llm)[-1]
    engine.step("응")
    engine.step("응")
    assert "놓친 기차" in prompts(llm)[-1]  # 3턴 유지
    engine.step("응")
    assert "놓친 기차" not in prompts(llm)[-1]


def test_past_not_injected_without_recall():
    engine, llm = make(scripted(2))
    engine.step(None)
    assert "청량리역" not in prompts(llm)[-1]  # 상세 기억은 검색될 때만


def test_refusal_is_logged_not_hidden():
    engine, _ = make({"character": [RefusalError("character")], "analysis": [ANALYSIS]})
    assert engine.step(None) == "(응답 없음)"
    assert any(e["kind"] == "refused" for e in engine.log.events)


def test_no_directive_vocabulary_in_character_prompt():
    engine, llm = make(scripted(1))
    engine.step(None)
    assert "지금 할 일" not in prompts(llm)[-1]


def test_history_is_real_multiturn_and_moment_not_kept():
    engine, llm = make(scripted(3))
    engine.state.clock = parse_hhmm("19:00")
    engine.step(None)
    engine.step("안녕")
    messages = [c for c in llm.calls if c["label"] == "character"][-1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "[이번 순간]" in messages[-1]["content"] and "[이번 순간]" not in messages[0]["content"]
    assert engine.transcript[0]["role"] == "assistant"
    assert all("[이번 순간]" not in m["text"] for m in engine.transcript)


def test_cover_lives_in_hidden_and_seed_has_no_hidden_content():
    engine, _ = make()
    assert all(h["cover"] in engine.system for h in engine.card["hidden"])
    assert "둘의 장래" in engine.topics and "앞으로의 계획" not in engine.topics
    seeds = " ".join(s.summary for b in engine.episodes.bigs.values() for s in b.smalls)
    assert "후기" not in seeds and "얼버무" not in seeds and "잠들" not in seeds


def test_world_events_remembered_and_visible_to_user():
    engine, llm = make(scripted(4))
    engine.state.clock = parse_hhmm("19:00")
    engine.state.last_meal = parse_hhmm("18:00")
    engine.step(None)
    assert engine.new_facts == ["해가 지면서 바람이 제법 차가워졌다."]
    engine.step("춥다")
    assert engine.new_facts == []
    engine.step("응")
    assert [e["id"] for e in engine.world_log] == ["evening_cold"]
    assert "19:00 해가 지면서" in [c for c in llm.calls if c["label"] == "character"][-1]["system"]


def test_plan_trigger_arrives_as_json_string():
    item = {"id": "rain", "inject": "비가 온다", "bound_to": "place", "deferrable": False, "deadline": None,
            "trigger": '{"clock_gte": "19:00"}'}
    engine = Engine(ScriptedLLM({"plan": [{"checkpoints": [item, {**item, "id": "bad", "trigger": "어두워지면"}], "details": []}] * 2}), "warm")
    assert [c.id for c in engine.plan] == ["rain"] and engine.plan[0].trigger == {"clock_gte": "19:00"}


def test_close_storyline_compresses_remaining_and_is_idempotent():
    closed = {"last_title": "작별", "last_summary": "정문에서 헤어졌다.", "oneline": "오늘 데이트", "relationship": "더 가까워졌다"}
    engine, llm = make({**scripted(2), "close": [closed]})
    engine.step(None)
    engine.step("잘 가")
    engine.close_storyline()
    engine.close_storyline()
    assert len(prompts(llm, "close")) == 1
    assert engine.transcript == [] and engine.episodes.today_titles() == ["작별"]
    assert engine.episodes.today.oneline == "오늘 데이트" and engine.relationship.state == "더 가까워졌다"


def test_state_survives_between_calls(tmp_path, monkeypatch):
    from storychat import play

    monkeypatch.setattr(play, "RUNS", tmp_path)
    engine, llm = make(scripted(3))
    engine.step(None)
    play.save(engine, "x")
    assert engine.llm is llm  # 저장 뒤에도 호출 중인 엔진은 그대로다
    restored = __import__("pickle").loads(play.state_path("x").read_bytes())
    assert restored.state.turn == 1 and restored.transcript == engine.transcript and restored.llm is None


def test_spoken_hidden_stops_rising_but_stays_known():
    fact = "후기를 미리 찾아 읽어"
    tense_movie = {**JUDGE, "tension": True, "topics": ["영화"]}
    fake = {**ANALYSIS, "hidden_spoken": [{"id": "movies", "quote": "지어낸 인용"}]}
    real = {**ANALYSIS, "hidden_spoken": [{"id": "movies", "quote": "영화 별로 안 좋아해"}]}
    hinted_then_said = {"judge": [tense_movie, JUDGE, JUDGE], "analysis": [ANALYSIS, fake, real, ANALYSIS],
                        "character": ["안녕", "음.", "사실 나 영화 별로 안 좋아해.", "응."]}
    engine, llm = make(hinted_then_said)
    engine.step(None)
    engine.step("영화 얘기만 하면 왜 그래")
    assert engine.spoken == set()  # 대사에 없는 인용은 인정하지 않는다
    engine.step("진짜?")
    assert engine.spoken == {"movies"}
    engine.step("그랬구나")
    call = [c for c in llm.calls if c["label"] == "character"][-1]
    assert "올라오는 것" not in call["messages"][-1]["content"]
    assert fact in call["system"] and "이미 상대에게 털어놓은 것" in call["system"]


def test_quote_may_skip_gesture_between_sentences():
    reply = "...부산 지사. 이번 달 안에 답해야 된대. (숨을 내쉰다) 나 아직 말 안 했어."
    assert quoted("이번 달 안에 답해야 된대. 나 아직 말 안 했어.", reply)
    assert quoted("(숨을 내쉰다) 나 아직", reply)
    assert not quoted("나 이미 말했어", reply)


def test_payoff_is_also_considered_when_world_just_changed():
    engine, llm = make({"judge": [JUDGE, JUDGE], "analysis": [{**ANALYSIS, "shown_said_quote": "사람들 많다"}, ANALYSIS, ANALYSIS],
                        "character": ["저기 사람들 많다.", "응.", "응."], "payoff": [{"detail_id": None, "how": "dialogue", "fact": ""}]})
    engine.last_seed_turn = -5
    engine.step(None)
    engine.step("응")
    assert prompts(llm, "payoff") == []  # 평온하고 세계도 그대로면 살피지 않는다
    engine.state.clock = parse_hhmm("19:00")
    engine.step("춥다")
    assert len(prompts(llm, "payoff")) == 1


def test_plan_knows_what_already_happened():
    engine, llm = make({"judge": [JUDGE], "analysis": [{**ANALYSIS, "action_type": "move", "move_to": "광장"}]})
    engine.world_log.append({"turn": 1, "clock": "17:40", "id": "parade", "text": "퍼레이드가 시작된다."})
    engine.step(None)
    plan = prompts(llm, "plan")[-1]
    assert "퍼레이드가 시작된다." in plan and "불꽃이 터지기" in plan


def test_short_segments_wait_and_compress_sees_only_recent_talk():
    summary = {"title": "t", "summary": "s", "relationship": "r"}
    n = KEEP_RAW_TURNS + 8
    engine, llm = make({"judge": [JUDGE] * n, "analysis": [ANALYSIS] * n, "alive": [{"alive": False}], "compress": [summary]})
    engine.boundaries |= {2, 3, 4}
    for i in range(KEEP_RAW_TURNS + 3):
        engine.step(None if i == 0 else f"말{i}")
    alive = next(c["prompt"] for c in llm.calls if c["label"] == "alive")
    written = next(c["prompt"] for c in llm.calls if c["label"] == "compress")
    assert engine.raw_start == 4  # T2, T3 경계는 건너뛰고 최소 길이를 채운 T4에서 자른다
    assert f"말{KEEP_RAW_TURNS + 2}" in alive and "말5" not in alive  # 판단은 최근 대화만 본다
    assert "말2" in written and "말3" not in written  # 요약은 그 구간만 본다
