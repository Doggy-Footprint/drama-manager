"""LLM 없는 판정 테스트. 캐릭터의 행동이 아니라 코드가 정하는 것만 검사한다."""
import pickle

import pytest

from storychat import play
from storychat.director import Director, _parse_scene, review_schema, scene_problems
from storychat.engine import Engine
from storychat.llm import RefusalError, ScriptedLLM
from storychat.memory import KEEP_RAW_TURNS, DetailLedger, quoted
from storychat.schema import TriggerContext, evaluate, parse_hhmm, topics_in, validate_trigger

REVIEW = {"tension": False, "branch": None, "topics": [], "recall_small": [], "action_type": "short_talk", "move_to": None,
          "relationship_shift": None, "relationship_note": None, "parted": False, "shown_said_quote": None, "hidden_spoken": []}
TENSE = {**REVIEW, "tension": True}
SCENE = {
    "intro": "저녁 놀이동산이다. 정문, 매점, 대관람차가 있다.",
    "places": ["정문", "매점", "대관람차"],
    "checkpoints": [
        {"id": "evening_cold", "trigger": {"clock_gte": "19:00"}, "inject": "해가 지면서 바람이 차가워졌다."},
        {"id": "fireworks", "trigger": {"clock_gte": "20:00"}, "inject": "하늘에 불꽃이 터지기 시작했다.", "deferrable": True, "deadline": "20:30"},
    ],
    "foreshadows": [{"id": "umbrella", "detail": "안내소 앞에 주인 없는 빨간 우산이 있다.", "shown_when": {"state_eq": {"place": "정문"}},
                     "trigger": {"signal": "topic:음식"}, "consequence": "안내소 직원이 빨간 우산을 들고 주인을 찾는다."}],
    "details": [{"text": "풍선 파는 사람이 하품을 한다.", "shown_when": {"state_eq": {"place": "매점"}}}],
}
TRANSFER = "부산 지사로 옮기는 제안"
FEELINGS = "상대를 좋아한다"


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


def test_quote_ignores_spacing_and_gesture():
    assert quoted("사람들 많다", "공연하나봐. 사람들  많다!")
    assert not quoted("사람 없다", "사람들 많다")
    assert not quoted("", "무엇이든")
    reply = "...부산 지사. 이번 달 안에 답해야 된대. (숨을 내쉰다) 나 아직 말 안 했어."
    assert quoted("이번 달 안에 답해야 된대. 나 아직 말 안 했어.", reply)
    assert quoted("(숨을 내쉰다) 나 아직", reply)
    assert not quoted("나 이미 말했어", reply)


def make(responses=None, mode="structured", scene=None):
    llm = ScriptedLLM(responses)
    return Engine(llm, "tsundere_some", scene=scene or SCENE, mode=mode), llm


def prompts(llm, label="character"):
    return [c["system"] + c["prompt"] for c in llm.calls if c["label"] == label]


def last_moment(llm):
    return [c for c in llm.calls if c["label"] == "character"][-1]["messages"][-1]["content"]


def seen_next_turn(engine, *texts):
    for text in texts:
        engine.step(text)


def test_signal_applies_next_turn_not_same_turn():
    engine, llm = make({"review": [{**REVIEW, "topics": ["둘의 장래"]}, REVIEW]})
    engine.step("우리 앞으로 어떻게 될까")
    assert all(TRANSFER not in p for p in prompts(llm))  # 판단은 응답 뒤라 이번 턴에는 아직 모른다
    assert engine.revealed == []
    engine.step("대답해 봐")
    assert TRANSFER in last_moment(llm) and "지금까지 숨긴 이유" in last_moment(llm)
    assert TRANSFER not in llm.calls[0]["system"] + llm.calls[0]["prompt"]


def test_signal_does_not_last_beyond_next_turn():
    engine, llm = make({"review": [{**REVIEW, "topics": ["회사 일"]}, TENSE, REVIEW]})
    engine.step("회사 어때")
    engine.step("왜 그래")
    engine.step("아까부터")   # 화제와 긴장이 서로 다른 턴이면 열리지 않는다
    assert engine.revealed == []


def test_hidden_needs_conditions_and_review_cannot_see_it_early():
    engine, llm = make({"review": [{**TENSE, "topics": ["회사 일"]}]})
    engine.step("요즘 왜 이렇게 답이 늦어")
    assert all(TRANSFER not in c["prompt"] + c["system"] for c in llm.calls)
    engine.step("말해 봐")
    assert TRANSFER in last_moment(llm)
    assert TRANSFER in [c for c in llm.calls if c["label"] == "review"][-1]["prompt"]  # 열린 뒤에는 말했는지 확인해야 한다


def test_spoken_hidden_stops_rising_but_stays_known():
    tense_work = {**TENSE, "topics": ["회사 일"]}
    fake = {**REVIEW, "hidden_spoken": [{"id": "transfer", "quote": "지어낸 인용"}]}
    real = {**REVIEW, "hidden_spoken": [{"id": "transfer", "quote": "나 부산 가게 될지도"}]}
    engine, llm = make({"review": [tense_work, fake, real], "character": ["안녕", "음.", "사실 나 부산 가게 될지도.", "응."]})
    engine.step(None)
    engine.step("말해 봐")
    assert engine.spoken == set()  # 대사에 없는 인용은 인정하지 않는다
    engine.step("진짜?")
    assert engine.spoken == {"transfer"}
    engine.step("그랬구나")
    call = [c for c in llm.calls if c["label"] == "character"][-1]
    assert "올라오는 것" not in call["messages"][-1]["content"]
    assert TRANSFER in call["system"] and "이미 상대에게 털어놓은 것" in call["system"]


def test_checkpoint_fires_once_and_defers_on_tension():
    engine, _ = make({"review": [REVIEW] * 3})
    engine.state.clock = parse_hhmm("20:00")
    engine.step(None)
    engine.step("와")
    engine.step("예쁘다")
    fired = [e["id"] for e in engine.log.events if e["kind"] == "checkpoint_fired"]
    assert fired.count("fireworks") == 1 and fired.count("evening_cold") == 1
    assert engine.new_facts == []

    engine, _ = make({"review": [TENSE, TENSE]})
    engine.state.clock = parse_hhmm("19:55")
    engine.step("할 말 있어")     # 직전 긴장이 이번 턴에 없다. 아직 20시 전
    engine.state.clock = parse_hhmm("20:05")
    engine.step("저기")
    assert "fireworks" not in engine.fired  # 긴장 중이면 미룬다
    engine.state.clock = parse_hhmm("20:30")
    engine.step("저기")
    assert "fireworks" in engine.fired  # 한도에 닿으면 터진다


def test_world_event_seen_once_by_user_and_twice_by_character():
    engine, llm = make({"review": [REVIEW] * 4})
    engine.state.clock = parse_hhmm("19:00")
    engine.step(None)
    assert engine.new_facts == ["해가 지면서 바람이 차가워졌다."]
    assert "방금 세계에 일어난 일" in last_moment(llm)
    engine.step("춥다")
    assert engine.new_facts == [] and "조금 전 세계에 일어난 일" in last_moment(llm)
    engine.step("응")
    assert "해가 지면서" not in last_moment(llm)
    assert "해가 지면서" in [c for c in llm.calls if c["label"] == "character"][-1]["system"]


def test_foreshadow_paid_with_consequence_when_planted_long_enough():
    planted = {**REVIEW, "shown_said_quote": "빨간 우산"}
    food = {**REVIEW, "topics": ["음식"]}
    engine, llm = make({"review": [planted, REVIEW, REVIEW, REVIEW, REVIEW, food, REVIEW, REVIEW],
                        "character": ["저기 빨간 우산 있네."]})
    engine.last_seed_turn = -5
    engine.step(None)
    assert engine.ledger.items["umbrella"].status == "planted"
    for i in range(5):
        engine.step(f"말{i}")
    assert engine.ledger.items["umbrella"].status == "planted"
    engine.step("뭐 먹지")
    assert engine.ledger.items["umbrella"].status == "paid"
    assert "안내소 직원이" in last_moment(llm) and "안내소 직원이" in engine.new_facts[0]
    assert next(e for e in engine.log.events if e["kind"] == "detail_paid")["gap"] == 6
    engine.step("저거")
    assert sum(e["kind"] == "detail_paid" for e in engine.log.events) == 1


def test_foreshadow_too_young_waits():
    planted = {**REVIEW, "shown_said_quote": "빨간 우산"}
    food = {**REVIEW, "topics": ["음식"]}
    engine, _ = make({"review": [planted, food], "character": ["저기 빨간 우산 있네."]})
    engine.last_seed_turn = -5
    engine.step(None)
    engine.step("뭐 먹지")
    engine.step("글쎄")
    assert engine.ledger.items["umbrella"].status == "planted"  # 살포 직후라 회수하지 않는다


def test_foreshadow_dropped_when_trigger_comes_before_planting():
    food = {**REVIEW, "topics": ["음식"]}
    engine, llm = make({"review": [food, REVIEW]})
    engine.step("뭐 먹지")   # 씨앗 간격 때문에 아직 보이지 않았다
    engine.step("글쎄")
    assert engine.ledger.items["umbrella"].status == "dropped"
    assert "안내소 직원이" not in last_moment(llm)
    engine.step("저기")
    assert "빨간 우산" not in last_moment(llm)  # 버린 떡밥은 다시 보여 주지 않는다


def test_seed_needs_real_quote_and_shown_when():
    engine, llm = make({"review": [{**REVIEW, "shown_said_quote": "지어낸 인용"}], "character": ["오늘 좋다."]})
    engine.last_seed_turn = -5
    engine.step(None)
    assert "빨간 우산" in last_moment(llm)
    assert engine.ledger.items["umbrella"].status == "seed"
    assert "풍선" not in last_moment(llm)  # 다른 장소의 디테일은 보이지 않는다


def test_review_has_no_path_to_add_details():
    engine, _ = make({"review": [{**REVIEW, "new_details": [{"text": "지어낸 과거", "source": "character", "quote": "안녕", "affordances": []}]}],
                      "character": ["안녕"]})
    before = set(engine.ledger.items)
    engine.step(None)
    assert set(engine.ledger.items) == before
    assert "new_details" not in review_schema(["정문"], ["영화"])["properties"]


def test_place_is_a_closed_list():
    engine, _ = make({"review": [{**REVIEW, "action_type": "move", "move_to": "식당가"}, {**REVIEW, "action_type": "move", "move_to": "매점"}]})
    engine.step(None)
    assert engine.state.place == "정문" and any(e["kind"] == "move_rejected" for e in engine.log.events)
    engine.step("매점 가자")
    assert engine.state.place == "매점"
    assert review_schema(["정문", "매점"], [])["properties"]["move_to"]["anyOf"][0]["enum"] == ["정문", "매점"]


def test_confession_updates_relationship_memory_that_turn():
    note = "상대가 좋아한다고 말했고 나는 아직 답하지 못했다."
    engine, llm = make({"review": [REVIEW, {**REVIEW, "relationship_note": note}, REVIEW]})
    engine.step(None)
    engine.step("나 너 좋아해")
    assert engine.relationship.state == note and 2 in engine.boundaries
    change = next(e for e in engine.log.events if e["kind"] == "relationship_updated")
    assert change["before"].startswith("서로 마음이") and change["state"] == note
    engine.step("대답은?")
    assert note in [c for c in llm.calls if c["label"] == "character"][-1]["system"]


def test_confession_turn_counts_as_branch_and_opens_feelings():
    note = "상대가 좋아한다고 말했다."
    engine, llm = make({"review": [{**REVIEW, "topics": ["우리 관계"], "relationship_note": note}, REVIEW]})
    engine.step("나 너 좋아해")
    assert engine.pending.branch == "관계 변화"
    engine.step("대답해 줘")
    assert FEELINGS in last_moment(llm)

    engine, llm = make({"review": [{**TENSE, "topics": ["우리 관계"]}, REVIEW]})
    engine.step("너 저번 주말에 왜 답 안 했어")
    engine.step("서운했어")
    assert FEELINGS not in last_moment(llm)  # 서운함만으로는 마음이 올라오지 않는다


def test_relationship_shift_changes_frame_and_memory():
    engine, _ = make({"review": [{**REVIEW, "relationship_shift": "오늘부터 사귀는 사이"}]})
    engine.step("사귀자")
    assert engine.state.relationship == "오늘부터 사귀는 사이"
    assert engine.relationship.state == "오늘부터 사귀는 사이"
    assert any(e["kind"] == "frame_changed" and e["axis"] == "relationship" for e in engine.log.events)


def run_until_compressed(responses, boundary=4):
    n = KEEP_RAW_TURNS + boundary + 2
    engine, llm = make({"review": [REVIEW] * n, **responses})
    engine.boundaries.add(boundary)
    for i in range(n):
        engine.step(None if i == 0 else f"말{i}")
        if prompts(llm, "compress"):
            break
    return engine, llm


def test_compress_rewrites_today_summary_and_keeps_recent_raw():
    first = {"title": "정문", "summary": "정문에서 만났다.", "today_summary": "오늘 정문에서 만났다.", "relationship": "새 설명"}
    engine, llm = run_until_compressed({"alive": [{"alive": False}], "compress": [first]})
    assert engine.raw_start == 4 and engine.episodes.today_titles() == ["정문"]
    assert engine.episodes.today_summary == "오늘 정문에서 만났다."
    assert engine.relationship.state == "새 설명"
    update = next(e for e in engine.log.events if e["kind"] == "today_summary_updated")
    assert update["before"] == "" and update["after"] == "오늘 정문에서 만났다."
    engine.step("다음")
    final = prompts(llm)[-1]
    assert "말1\n" not in final and "오늘 정문에서 만났다." in final and "정문에서 만났다." in final
    engine.episodes.revise_today("오늘 달라졌다.", 99)
    assert engine.episodes.revisions[-1]["before"] == "오늘 정문에서 만났다."
    assert engine.episodes.today.smalls[0].summary == "정문에서 만났다."  # 지나간 에피소드는 고치지 않는다


def test_compress_sees_only_the_segment_and_previous_summary():
    first = {"title": "t", "summary": "s", "today_summary": "요약", "relationship": "r"}
    engine, llm = run_until_compressed({"alive": [{"alive": False}], "compress": [first]})
    alive = next(c["prompt"] for c in llm.calls if c["label"] == "alive")
    written = next(c["prompt"] for c in llm.calls if c["label"] == "compress")
    assert "상대: 말1" in written and "상대: 말2" in written and "말3" not in written
    assert "말8" in alive and "[지금까지의 오늘 요약]\n없음" in written


def test_old_segment_cannot_overwrite_newer_relationship():
    note = "오늘부터 사귄다."
    responses = [REVIEW] * 6 + [{**REVIEW, "relationship_note": note}] + [REVIEW] * 6
    first = {"title": "t", "summary": "s", "today_summary": "요약", "relationship": "아직 썸이다."}
    engine, llm = make({"review": responses, "alive": [{"alive": False}], "compress": [first]})
    engine.boundaries.add(4)
    for i in range(10):
        engine.step(None if i == 0 else f"말{i}")
    assert prompts(llm, "compress") and engine.relationship.state == note
    assert any(e["kind"] == "relationship_kept" for e in engine.log.events)


def test_compress_update_does_not_block_later_compress():
    engine, _ = make()
    engine.relationship.update("압축이 쓴 설명", "today-1", 9)
    assert not engine.relationship.changed_since(8)
    engine.relationship.update("고백 뒤", "turn", 10)
    assert engine.relationship.changed_since(8)


def test_alive_segment_is_postponed():
    n = KEEP_RAW_TURNS + 6
    engine, llm = make({"review": [REVIEW] * n, "alive": [{"alive": True}]})
    engine.boundaries.add(4)
    for i in range(KEEP_RAW_TURNS + 3):
        engine.step(None if i == 0 else f"말{i}")
    assert len(prompts(llm, "alive")) == 1 and not prompts(llm, "compress") and engine.raw_start == 1
    assert engine.compress_after > engine.state.turn


def test_recall_from_review_is_small_id_and_expires():
    engine, llm = make({"review": [{**REVIEW, "recall_small": ["trip-1", "없는것"]}, REVIEW, REVIEW, REVIEW, REVIEW]})
    engine.step("강릉 기억나?")
    assert "놓친 기차" not in last_moment(llm)  # 판단이 응답 뒤라 다음 턴부터 떠오른다
    for _ in range(3):
        engine.step("응")
        assert "놓친 기차" in last_moment(llm)
    engine.step("응")
    assert "놓친 기차" not in last_moment(llm)
    assert not prompts(llm, "drill")


def test_past_not_injected_without_recall():
    engine, llm = make()
    engine.step(None)
    assert "청량리역" not in prompts(llm)[-1]


def test_review_refusal_keeps_going():
    engine, _ = make({"character": [RefusalError("character")], "review": [RefusalError("review")]})
    assert engine.step(None) == "(응답 없음)"
    assert sum(e["kind"] == "refused" for e in engine.log.events) == 2
    assert engine.state.clock == parse_hhmm("17:35")


def test_one_review_per_turn_without_other_judgement_calls():
    engine, llm = make({"review": [REVIEW] * 3})
    for text in (None, "안녕", "뭐 탈까"):
        engine.step(text)
    assert [c["label"] for c in llm.calls] == ["character", "review"] * 3


def test_history_is_real_multiturn_and_moment_not_kept():
    engine, llm = make({"review": [REVIEW] * 2})
    engine.state.clock = parse_hhmm("19:00")
    engine.step(None)
    engine.step("안녕")
    messages = [c for c in llm.calls if c["label"] == "character"][-1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "[이번 순간]" in messages[-1]["content"] and "[이번 순간]" not in messages[0]["content"]
    assert engine.transcript[0]["role"] == "assistant"
    assert all("[이번 순간]" not in m["text"] for m in engine.transcript)


def test_baseline_injects_nothing_and_only_calls_character():
    engine, llm = make(mode="baseline")
    engine.state.clock = parse_hhmm("20:30")
    engine.step(None)
    engine.step("우리 앞으로 어떻게 될까")
    engine.step("회사는?")
    assert {c["label"] for c in llm.calls} == {"character"}
    text = " ".join(c["system"] + c["prompt"] for c in llm.calls)
    for absent in (TRANSFER, FEELINGS, "불꽃이 터지기", "[이번 순간]\n", "빨간 우산", "청량리역"):
        assert absent not in text
    assert engine.scene["intro"] in text and "서로 마음이" in text and "요즘 회사 일이 조금 바쁘다" in text
    assert engine.fired == set() and engine.new_facts == []
    assert len(engine.transcript) == 5
    assert "방금 정문에서 만났다" in llm.calls[0]["prompt"]  # 시작 장소는 두 모드가 같다


def test_baseline_output_has_no_status_line():
    engine, _ = make({"character": ["안녕."]}, mode="baseline")
    reply = engine.step(None)
    assert play.view(engine, reply) == "서진: 안녕."
    engine, _ = make({"character": ["안녕."], "review": [REVIEW]})
    assert play.view(engine, engine.step(None)).startswith("[")


def test_baseline_close_makes_no_call():
    engine, llm = make(mode="baseline")
    engine.step(None)
    calls = len(llm.calls)
    engine.close_storyline()
    assert len(llm.calls) == calls and engine.closed


def test_close_storyline_rewrites_summary_and_is_idempotent():
    closed = {"last_title": "작별", "last_summary": "정문에서 헤어졌다.", "today_summary": "오늘 고백했다.", "oneline": "오늘", "relationship": "더 가까워졌다"}
    engine, llm = make({"review": [REVIEW] * 2, "close": [closed]})
    engine.step(None)
    engine.step("잘 가")
    engine.close_storyline()
    engine.close_storyline()
    assert len(prompts(llm, "close")) == 1
    assert engine.transcript == [] and engine.episodes.today_titles() == ["작별"]
    assert engine.episodes.today_summary == "오늘 고백했다." and engine.relationship.state == "더 가까워졌다"
    assert any(e["kind"] == "today_summary_updated" for e in engine.log.events)


def test_state_survives_between_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(play, "RUNS", tmp_path)
    engine, llm = make({"review": [TENSE]})
    engine.step(None)
    play.save(engine, "x")
    assert engine.llm is llm  # 저장 뒤에도 호출 중인 엔진은 그대로다
    restored = pickle.loads(play.state_path("x").read_bytes())
    assert restored.state.turn == 1 and restored.transcript == engine.transcript and restored.llm is None
    assert restored.pending.tension  # 다음 턴에 적용할 판단도 이어진다


def test_scene_name_placeholder_follows_card():
    scene = {**SCENE, "checkpoints": [{"id": "call", "trigger": {"clock_gte": "19:00"}, "inject": "{name}에게 전화가 온다."},
                                      SCENE["checkpoints"][1]]}
    engine, _ = make(scene=scene)
    assert engine.checkpoints[0].inject == "서진에게 전화가 온다."
    female = Engine(ScriptedLLM(), "tsundere_some_f", scene=scene)
    assert female.checkpoints[0].inject == "수아에게 전화가 온다." and "너는 수아다" in female.system
    assert "{name}" not in str(Engine(ScriptedLLM(), "tsundere_some_f").scene)


def test_ledger_tracks_only_scene_items():
    ledger = DetailLedger(SCENE)
    assert set(ledger.items) == {"umbrella", "detail_1"}
    assert ledger.items["umbrella"].kind == "foreshadow" and ledger.items["detail_1"].kind == "detail"


@pytest.mark.parametrize("change", [
    {"places": []},
    {"checkpoints": [{"id": "x", "trigger": {"mood": "슬픔"}, "inject": "a"}]},
    {"checkpoints": [{"id": "x", "trigger": {"state_eq": {"place": "식당가"}}, "inject": "a"}]},
    {"foreshadows": [{"id": "x", "detail": "a", "shown_when": {"after": "없는것"}, "trigger": {"clock_gte": "19:00"}, "consequence": "b"}]},
])
def test_bad_scene_is_refused(change):
    assert scene_problems({**SCENE, **change})
    with pytest.raises(ValueError):
        Engine(ScriptedLLM(), "tsundere_some", scene={**SCENE, **change})


def test_scene_draft_parses_json_strings_and_retries():
    raw = {"intro": "i", "places": ["정문"], "foreshadows": [], "details": [{"text": "t", "shown_when": '{"clock_gte": "18:00"}'}]}
    bad = {**raw, "checkpoints": [{"id": "x", "inject": "a", "deferrable": False, "deadline": None, "trigger": "어두워지면"}]}
    good = {**raw, "checkpoints": [{"id": "x", "inject": "a", "deferrable": False, "deadline": None, "trigger": '{"clock_gte": "19:00"}'}]}
    llm = ScriptedLLM({"scene_draft": [bad, good]})
    scene, problems = Director(llm).scene_draft("brief")
    assert problems == [] and scene["checkpoints"][0]["trigger"] == {"clock_gte": "19:00"}
    assert scene["details"][0]["shown_when"] == {"clock_gte": "18:00"}
    assert "문제가 있었다" in llm.calls[1]["prompt"]
    assert _parse_scene(good)["places"] == ["정문"]


def test_world_event_is_staged_to_the_situation_and_core_is_kept():
    staged = "정문 쪽으로 걸어가는데 머리 위로 바람이 차갑게 불어온다."
    engine, llm = make({"stage": [{"text": staged}], "review": [REVIEW] * 2})
    engine.step(None)
    engine.state.clock = parse_hhmm("19:00")
    engine.step("어디 갈까")
    stage = next(c for c in llm.calls if c["label"] == "stage")
    assert "해가 지면서 바람이 차가워졌다." in stage["prompt"] and "상대: 어디 갈까" in stage["prompt"]
    assert engine.new_facts == [staged] and staged in last_moment(llm)
    fired = next(e for e in engine.log.events if e["kind"] == "checkpoint_fired")
    assert fired["inject"] == staged and fired["core"] == "해가 지면서 바람이 차가워졌다."


def test_stage_that_drops_keep_words_is_retried_then_falls_back():
    scene = {**SCENE, "checkpoints": [{**SCENE["checkpoints"][0], "keep": ["바람"]}, SCENE["checkpoints"][1]]}
    engine, llm = make({"stage": [{"text": "공기가 달라진다."}, {"text": "정문 쪽으로 찬 바람이 분다."}], "review": [REVIEW]}, scene=scene)
    engine.state.clock = parse_hhmm("19:00")
    engine.step(None)
    assert engine.new_facts == ["정문 쪽으로 찬 바람이 분다."]
    assert "빠진 말: 바람" in [c for c in llm.calls if c["label"] == "stage"][1]["prompt"]

    engine, _ = make({"stage": [{"text": "공기가 달라진다."}, {"text": "어두워진다."}], "review": [REVIEW]}, scene=scene)
    engine.state.clock = parse_hhmm("19:00")
    engine.step(None)
    assert engine.new_facts == ["해가 지면서 바람이 차가워졌다."]
    assert sum(e["kind"] == "stage_rejected" for e in engine.log.events) == 2


def test_stage_failure_falls_back_to_core():
    engine, _ = make({"stage": [RefusalError("stage")], "review": [REVIEW]})
    engine.state.clock = parse_hhmm("19:00")
    engine.step(None)
    assert engine.new_facts == ["해가 지면서 바람이 차가워졌다."]
