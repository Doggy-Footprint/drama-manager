"""코드로 지키는 규칙과 턴 흐름. LLM은 가짜로 바꾼다."""
import random

import pytest

from park import session as session_mod
from park.state import Character, Details, Memory, World, load, scenarios, to_min


def test_time_and_battery_only_move_forward():
    w = World.fresh()
    w.advance(10, "idle")
    assert w.clock == "18:55" and w.battery == pytest.approx(23 - 1.5)
    w.advance(-30, "idle")  # 거꾸로 가지 않는다
    assert w.clock == "18:55"
    notes = w.advance(30, "calling")  # 1.2%/분이면 30분 안에 꺼진다
    assert not w.phone_on and w.battery == 0 and "꺼졌다" in notes[0]
    assert to_min(w.phone_off_at) < to_min(w.clock)


def test_event_fires_once_inside_window_including_skipped_time():
    w = World.fresh()
    assert not w.fire("fireworks", 1, "")[0]          # 18:45, 창 밖
    w.advance(40, "idle")                             # 18:45 → 19:25, 미아 방송 창(19:15~19:35)을 지나간다
    assert w.fire("lost_child_announcement", 2, "")[0]
    assert not w.fire("lost_child_announcement", 3, "")[0]  # 두 번은 안 된다
    w.minute = to_min("20:05")
    w.advance(5, "idle")                              # 20:05 → 20:10 구간은 음악분수(19:30~19:40)와 겹치지 않는다
    assert not w.fire("fountain_show", 4, "")[0] and "fountain_show" not in w.fired


def test_core_event_is_forced_after_five_minutes():
    w = World.fresh()
    w.minute = to_min("20:04")
    assert w.forced_core(1) == []
    w.minute = to_min("20:05")
    assert [e["id"] for e in w.forced_core(1)] == ["fireworks"]
    assert w.fired["fireworks"]["forced"]


def test_core_event_is_forced_even_when_a_turn_jumps_over_its_window():
    w = World.fresh("hometown")
    start, end = load("hometown", "world.yaml")["events"][0]["window"]   # blackout 22:00~22:20
    w.minute = to_min(start) - 2
    w.advance(30, "idle")                              # 21:58 → 22:28, 창을 통째로 건너뛴다
    assert to_min(w.clock) > to_min(end)
    assert [e["id"] for e in w.forced_core(1)] == ["blackout"]
    w2 = World.fresh("hometown")
    w2.minute = to_min(end) + 1
    w2.advance(5, "idle")                              # 창이 이미 지난 뒤 시작한 턴에는 일으키지 않는다
    assert w2.forced_core(1) == []


def test_expansion_adds_location_and_event_by_chance():
    w = World.fresh()
    exp = {"accept": True, "source": "유저", "kind": "location", "name": "옥상 정원", "text": "편의점 위 옥상", "near": "store",
           "event_text": "옥상에서 누가 기타를 친다."}
    rolls = [w.expand(dict(exp), t, random.Random(t)) for t in range(40)]
    with_event = [r for r in rolls if "event" in r]
    assert all(r["accepted"] for r in rolls)
    assert all(r["roll"] < 0.2 for r in with_event) and 0 < len(with_event) < 40
    assert any(v["name"] == "옥상 정원" for v in w.locations.values())


def test_detail_status_changes_only_when_realized():
    d = Details.fresh()
    assert d.get("late")["status"] == "planted" and d.get("battery")["status"] == "unplanted"
    ok, _, mode = d.attempt("battery", "payoff", "", 1)       # 안 뿌린 걸 거두려 하면 뿌리는 것으로 고친다
    assert ok and mode == "plant" and d.get("battery")["status"] == "unplanted"  # 고르기만 해서는 바뀌지 않는다
    assert d.realize("battery", 1, "수아")[0] and d.get("battery")["status"] == "planted"
    assert not d.realize("battery", 1, "수아")[0]             # 같은 시도를 두 번 확정하지 않는다
    d.attempt("battery", "payoff", "", 5)
    d.realize("battery", 5, "지문")
    assert d.get("battery")["status"] == "paid_off" and not d.attempt("battery", "mention", "", 6)[0]
    did = d.add_fact("유저는 오이를 싫어한다.", ["취향"], 4)
    assert d.get(did)["source"] == "유저" and d.get(did)["status"] == "planted"


def test_realized_detail_cools_down():
    d = Details.fresh()
    d.attempt("hill_bench", "plant", "", 2)
    assert "hill_bench" in [x["id"] for x in d.candidates(3)]   # 시도만 한 것은 쿨다운이 없다
    d.realize("hill_bench", 2, "수아")
    assert all("hill_bench" not in [x["id"] for x in d.candidates(t)] for t in (3, 4))
    assert "hill_bench" in [x["id"] for x in d.candidates(5)]
    d.attempt("hill_bench", "mention", "", 5)
    d.realize("hill_bench", 5, "수아")                          # 두 번째로 쓰이면 쿨다운이 6턴으로 늘어난다
    assert all("hill_bench" not in [x["id"] for x in d.candidates(t)] for t in (8, 10))
    assert "hill_bench" in [x["id"] for x in d.candidates(11)]


def test_facts_can_be_retired():
    w = World.fresh()
    fid = w.add_fact("언덕에 남은 벤치는 하나뿐이다.", "수아", 3, "hill")
    assert [f["id"] for f in w.active_facts()] == [fid]
    assert w.retire_fact(fid, 7)[0] and w.active_facts() == [] and not w.retire_fact(fid, 8)[0]


def test_character_update_touches_dynamic_only():
    c = Character.fresh()
    core = dict(c.data["core"])
    c.update({"mood": "서운함", "attitude": None, "want": None, "grievances": [{"id": "always_waiting", "intensity": 9}],
              "new_grievance": None}, 1)
    assert c.data["core"] == core and c.data["dynamic"]["mood"] == "서운함"
    assert c.data["dynamic"]["grievances"][0]["intensity"] == 3
    assert c.open_secret("lost_child", 2)[0] and not c.open_secret("lost_child", 3)[0]
    assert c.mark_revealed("confess", 4)[1].endswith("(열리기 전에 새어 나감)")


def test_episode_keeps_last_five_turns_raw():
    m = Memory.fresh(Character.fresh())
    m.turns = [{"n": i, "clock": "19:00"} for i in range(1, 11)]
    ok, _ = m.close_episode(9, "앞부분", "요약", ["수아는 기억한다."])
    assert ok and [t["n"] for t in m.turns] == [6, 7, 8, 9, 10]
    assert m.episodes[0]["turns"] == [1, 5] and m.long_term[-1] == "수아는 기억한다."


# ---------------- 턴 흐름 ----------------

class FakeLLM:
    def __init__(self, dm: dict, character: dict | None = None, facts: list | None = None):
        self.dm, self.character_out, self.facts = dm, character, facts or []
        self.calls, self.labels, self.docs = [], [], {}

    def json(self, system, user, schema, *, label, effort="low"):
        self.labels.append(label)
        self.docs[label] = user
        self.calls.append({"label": label, "input": 1, "cached": 0, "output": 1, "seconds": 0})
        if label == "retrieve-world":
            return {"picks": [], "facts": [{"id": f} for f in self.facts]}
        if label == "extract-facts":
            return {"items": []}
        if label == "retrieve-details":
            return getattr(self, "detail_picks", None) or {"need": "", "picks": []}
        if label == "drama-manager":
            return dict(self.dm)
        if label == "character":
            return dict(self.character_out)
        if label == "dm-episode":
            return getattr(self, "episode_out", None) or {"why": "", "close": False, "until_turn": 0, "title": "", "summary": "",
                                                          "details": [], "memories": []}
        raise AssertionError(label)


class MixedLanguageLLM(FakeLLM):
    """캐릭터가 처음엔 일본어를 섞고, 다시 쓰게 하면 rewrites 순서대로 답한다."""
    def __init__(self, dm, first, rewrites):
        super().__init__(dm, first)
        self.rewrites = list(rewrites)

    def json(self, system, user, schema, *, label, effort="low"):
        if label == "character-guard":
            self.labels.append(label)
            self.docs.setdefault("guard", []).append(user)
            self.calls.append({"label": label, "input": 1, "cached": 0, "output": 1, "seconds": 0})
            return {**self.character_out, "say": self.rewrites.pop(0)}
        return super().json(system, user, schema, label=label, effort=effort)


def test_guardrail_rewrites_reply_with_foreign_script_then_strips(runs):
    s = session_mod.Session(scenario="hometown", llm=MixedLanguageLLM(dm(), {**SAY, "say": "(隣に座る) 왔네."}, ["(옆에 앉는다) 왔네."]))
    entry = s.turn("(계단에 앉는다)")
    assert entry["reply"] == "(옆에 앉는다) 왔네."
    assert s.llm.labels.count("character-guard") == 1 and "<rewrite>" in s.llm.docs["guard"][0]
    assert any("다시 쓰게 함" in n["text"] for n in s.traces[1]["character_applied"])
    # 두 번 다시 써도 섞여 있으면 그 글자를 지운다
    s2 = session_mod.Session(scenario="hometown", llm=MixedLanguageLLM(dm(), {**SAY, "say": "まだ 여기 있을게."}, ["まだ 여기 있을게.", "여기 있을게、まだ"]))
    assert s2.turn("(손을 잡는다)")["reply"] == "여기 있을게"
    assert s2.llm.labels.count("character-guard") == 2


def test_guardrail_strips_foreign_script_from_narration_and_directive(runs):
    s = session_mod.Session(scenario="hometown", llm=FakeLLM(dm(narration="별이 또렷하다. ⟂", directive="곁에 있고 싶다。確かめたい"), SAY))
    s.turn("(하늘을 본다)")
    assert s.log[0]["narration"] == "별이 또렷하다." and s.traces[1]["dm"]["directive"] == "곁에 있고 싶다"
    assert "確" not in s.traces[1]["character"]["_doc"]


def dm(**over):
    base = {"scene_read": "", "minutes": 2, "minutes_why": "", "user_location": None, "character_location": None,
            "together": True, "channel": "face", "character_phone_use": "idle", "phone_charging": False, "offscreen": None, "beat": "setup",
            "tension": 2, "events": [], "details": [], "reveal": [], "directive": "",
            "character_update": {"mood": None, "attitude": None, "want": None, "grievances": [], "new_grievance": None},
            "goal_updates": [], "world_additions": [], "show_facts": [], "retire_facts": [],
            "user_facts": [], "narration": None}
    return {**base, **over}


SAY = {"say": "왔네.", "move": None, "phone_use": "idle", "used_details": [], "revealed": []}


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "ROOT", tmp_path)


def test_dm_cannot_send_suah_away_alone(runs):
    s = session_mod.Session(llm=FakeLLM(dm(character_location="hill", together=False), SAY))
    s.turn(None)
    assert s.world.char_loc == "gate" and any("수아 이동 거절" in n["text"] for n in s.traces[1]["applied"]["notes"])


def test_character_leaving_alone_moves_her_after_reply(runs):
    s = session_mod.Session(llm=FakeLLM(dm(), {**SAY, "move": {"to": "hill", "alone": True}}))
    s.turn("키링 줄 설게")
    assert s.world.char_loc == "hill" and not s.world.together


def test_phone_off_means_no_character_call(runs):
    llm = FakeLLM(dm(user_location="market", together=False, channel="phone"), SAY)
    s = session_mod.Session(llm=llm)
    s.world.phone_on, s.world.battery = False, 0
    entry = s.turn("(전화를 건다)")
    assert entry["reply"] is None and "character" not in llm.labels
    assert s.traces[1]["dm"]["channel"] == "none"


def test_user_facts_go_to_memory_and_detail_pool(runs):
    s = session_mod.Session(llm=FakeLLM(dm(user_facts=[{"text": "유저는 고소공포증이 있다.", "affordances": ["언덕", "배려"]}]), SAY))
    s.turn("나 사실 높은 데 무서워")
    assert "유저는 고소공포증이 있다." in s.memory.long_term
    assert any(d["source"] == "유저" for d in s.details.items)


def test_opening_turn_takes_no_time(runs):
    s = session_mod.Session(llm=FakeLLM(dm(minutes=15), SAY))
    s.turn(None)
    assert s.world.clock == "18:45"
    s.turn("가자")
    assert s.world.clock == "19:00"


def test_detail_left_to_suah_counts_only_if_she_uses_it(runs, monkeypatch):
    pick = {"need": "", "picks": [{"id": "battery", "use_as": ""}, {"id": "hill_bench", "use_as": ""}]}
    monkeypatch.setattr(session_mod.Session, "_retrieve_details", lambda self, u, n: pick)
    picks = [{"id": "battery", "mode": "plant", "via": "character", "how": "휴대폰을 흘끗 본다"},
             {"id": "hill_bench", "mode": "plant", "via": "narration", "how": "언덕 쪽 벤치가 보인다"}]
    s = session_mod.Session(llm=FakeLLM(dm(details=picks, narration="언덕 위에 벤치가 줄지어 있다."), SAY))
    s.turn(None)
    assert s.details.get("battery")["status"] == "unplanted"      # 수아가 쓰지 않았다
    assert s.details.get("hill_bench")["status"] == "planted"     # 지문으로 나왔다
    s.llm.character_out = {**SAY, "used_details": ["battery"]}
    s.llm.dm = dm(details=picks[:1])
    s.turn("폰 괜찮아?")
    assert s.details.get("battery")["status"] == "planted"


def test_detail_use_as_must_be_one_of_its_affordances(runs):
    s = session_mod.Session(llm=FakeLLM(dm(), SAY))
    aff = s.details.get("battery")["affordances"][0]
    s.llm.detail_picks = {"need": "", "picks": [{"id": "battery", "use_as": aff}, {"id": "hill_bench", "use_as": "숨긴 마음을 털어놓게"}]}
    out = s._retrieve_details("안녕", 1)
    assert [p["use_as"] for p in out["picks"]] == [aff, ""]      # 목록에 없는 해석은 DM에게 가지 않는다


def new_place(arrive):
    return {"source": "유저", "kind": "location", "name": "지하 주차장", "text": "본관 지하 주차장", "near": None,
            "arrive": arrive, "accept": True, "why": "", "event_text": "", "affordances": []}


def test_new_location_can_be_entered_in_the_same_turn(runs):
    s = session_mod.Session(llm=FakeLLM(dm(world_additions=[new_place("both")]), SAY))
    s.turn("주차장 가요")
    key = next(k for k, v in s.world.locations.items() if v["name"] == "지하 주차장")
    assert (s.world.user_loc, s.world.char_loc, s.world.together) == (key, key, True)


def test_new_location_does_not_send_character_away_alone(runs):
    s = session_mod.Session(llm=FakeLLM(dm(world_additions=[new_place("character")]), SAY))
    before = s.world.char_loc
    s.turn("주차장이 있대")
    assert s.world.char_loc == before and s.world.together                # 함께 있던 캐릭터를 혼자 보내지 않는다
    assert any("혼자" in x["text"] for x in s.traces[1]["applied"]["notes"] if not x["ok"])


def test_scene_fact_from_suah_is_kept_and_retrieved_not_broadcast(runs):
    add = {"source": "수아", "kind": "fact", "name": "", "text": "언덕에 남은 벤치는 하나뿐이다.", "near": "hill",
           "arrive": "none", "accept": True, "why": "", "event_text": "", "affordances": []}
    llm = FakeLLM(dm(world_additions=[add]), SAY)
    s = session_mod.Session(llm=llm)
    s.turn("저기 비었네")
    fid = s.world.facts[0]["id"]
    assert "남은 벤치는 하나뿐" in llm.docs["character"]              # 이번 턴에 새로 생긴 사실은 바로 보인다
    llm.dm = dm()
    s.turn("앉자")
    assert "남은 벤치는 하나뿐" not in llm.docs["drama-manager"]       # 검색되지 않으면 DM에게도 가지 않는다
    assert "남은 벤치는 하나뿐" not in llm.docs["character"]
    llm.facts, llm.dm = [fid], dm(show_facts=[fid], retire_facts=[{"id": fid}])
    s.turn("여기 앉자")
    assert "남은 벤치는 하나뿐" in llm.docs["drama-manager"] and not s.world.active_facts()


def test_session_survives_pickle_between_cli_turns(runs, monkeypatch):
    import json
    import pickle
    s = session_mod.Session(llm=FakeLLM(dm(), SAY), tag="cli")
    s.turn(None)
    monkeypatch.setattr(session_mod, "LLM", lambda: FakeLLM(dm(), SAY))
    s2 = pickle.loads(pickle.dumps(s))
    assert len(s2.llm.calls) == len(s.llm.calls) and s2.world.clock == s.world.clock
    s2.turn("가자")
    rows = [json.loads(x) for x in s2.record.read_text(encoding="utf-8").splitlines()]
    assert s2.record.name.endswith("_cli.jsonl") and [r["trace"]["n"] for r in rows] == [1, 2]
    assert rows[-1]["state"]["n"] == 2   # 웹 로그 뷰어가 마지막 줄의 state로 화면을 그린다


def test_charging_raises_battery_and_turns_phone_back_on():
    w = World.fresh()
    w.advance(30, "calling")                      # 23% → 0%, 꺼진다
    assert not w.phone_on
    w.charging = True
    notes = w.advance(5, "idle")                  # 꺼진 동안은 쓰지 않으니 충전만 된다
    assert w.phone_on and w.battery == pytest.approx(5.0) and any("다시 켜졌다" in x for x in notes)
    w.advance(10, "idle")                         # 켜진 채 충전하면 쓰는 만큼 덜 오른다
    assert w.battery == pytest.approx(5 + 10 * (1.0 - 0.15))
    w.charging = False
    w.advance(10, "idle")
    assert w.battery == pytest.approx(5 + 8.5 - 1.5)


def test_dm_charging_flag_is_applied(runs):
    s = session_mod.Session(llm=FakeLLM(dm(phone_charging=True, minutes=10), SAY))
    s.turn("보조배터리 써")
    assert s.world.charging and s.world.battery > 23
    assert "충전 중" in s.llm.docs["character"]


@pytest.mark.parametrize("scenario", [x["id"] for x in scenarios()])  # 새 시나리오도 자동으로 검사한다
def test_every_scenario_fills_prompts_and_runs_a_turn(runs, scenario):
    from park.state import scenarios
    assert scenario in [x["id"] for x in scenarios()]
    s = session_mod.Session(scenario=scenario, llm=FakeLLM(dm(), SAY))
    for name in ("dm", "character", "extract_facts", "retrieve_world", "retrieve_details", "episode", "baseline"):
        text = s.prompt(name)
        assert "{{" not in text and (scenario == "park" or "수아" not in text), name
    s.turn(None)
    assert s.log[0]["user"] == s.world.scenario["opening"] and s.log[0]["reply"] == SAY["say"]
    assert s.record.stem.split("_")[2] == scenario


# ---------------- 이벤트 스키마, 관계, 지난 만남 (overtime) ----------------

from park.state import Relationship, apply_effects, load_history, requires_ok, validate_schema  # noqa: E402


def overtime(llm=None, dm_out=None):
    return session_mod.Session(scenario="overtime", llm=llm or FakeLLM(dm_out or dm(), SAY))


def cand_ids(s):
    blocked = []
    return [e["id"] for e in s.world.event_candidates(60, gate=s.gate, blocked=blocked)], {b["id"]: b["why"] for b in blocked}


def test_legacy_scenarios_keep_working_without_new_fields(runs):
    for sc in ("park", "hometown", "surgeon"):
        s = session_mod.Session(scenario=sc, llm=FakeLLM(dm(), SAY))
        assert s.relation is None and s.world.flags == {} and s.world.items == {}
        assert not any(d.get("episode") for d in s.details.items) and s.memory.prior == []
        assert s.world.can_charge() == (True, "")                     # charge_from가 없으면 충전은 DM 판단 그대로
        assert s.world.event_candidates(60, gate=s.gate) == s.world.event_candidates(60)
        assert "<past_episodes>" not in s.prompt("dm") and "relationship_update" not in s.prompt("dm")
    schema = session_mod.dm_schema(name="수아", locations=["a"], event_ids=[], detail_ids=[], secret_ids=[], grievance_ids=[], goal_ids=[])
    assert "relationship_update" not in schema["properties"] and "item_moves" not in schema["properties"]


def test_requires_filters_candidates_with_reasons(runs):
    s = overtime()
    ids, blocked = cand_ids(s)
    assert "umbrella_spotted" in ids                                  # 유저가 우산을 들고 있고 약속이 open
    assert blocked["rooftop_ramen"] == "신뢰 2 < 3"                  # 관계 조건
    s.world.items["umbrella"]["holder"] = "character"                 # 우산을 돌려주면 그 이벤트는 더는 후보가 아니다
    ids, blocked = cand_ids(s)
    assert "umbrella_spotted" not in ids and "umbrella_spotted" in blocked
    s.relation.stats["trust"]["value"] = 3
    s.world.minute = to_min("20:55")
    assert "rooftop_ramen" in cand_ids(s)[0]
    s.relation.set_promise("dinner", "kept", "국밥을 샀다", 3)        # 기억 조건: 밥 약속이 이미 지켜졌으면 열리지 않는다
    assert "약속 dinner" in cand_ids(s)[1]["rooftop_ramen"]


def test_requires_vocabulary_covers_presence_items_flags_memory(runs):
    s = overtime()
    w, r, d = s.world, s.relation, s.details
    ok = lambda req: requires_ok(req, world=w, relation=r, details=d)[0]
    assert ok({"together": True}) and not ok({"together": False})
    assert ok({"char_at": ["office"]}) and not ok({"user_at": ["lobby"]})
    assert ok({"flags": {"weather": "맑음"}}) and not ok({"flags": {"weather": "비"}})
    assert ok({"has": {"umbrella": "either"}}) and not ok({"has": {"umbrella": "character"}})
    assert ok({"relation": {"stage_min": "overtime_mates", "max": {"flutter": 1}}}) and not ok({"relation": {"stage_min": "some"}})
    assert ok({"memory": {"not_recalled": ["ep1_banana"]}}) and not ok({"memory": {"recalled": ["ep1_banana"]}})
    assert not ok({"after": ["rain_starts"]})
    assert not requires_ok({"relation": {"min": {"trust": 1}}}, world=w)[0]   # 관계 상태가 없는 이야기에서는 통과 못 한다


def test_effects_change_flags_items_relation_promises_and_plant(runs):
    s = overtime()
    notes = apply_effects({"set": {"weather": "비"}, "give": {"power_bank": "character"}, "relation": {"trust": 1},
                           "promise": {"dinner": "kept"}, "plant": ["notebook_last_page"]},
                          world=s.world, relation=s.relation, details=s.details, turn=4, source="이벤트 x")
    assert all(ok for ok, _ in notes) and len(notes) == 5
    assert s.world.flags["weather"] == "비" and s.world.items["power_bank"]["holder"] == "character"
    assert s.relation.value("trust") == 3 and s.relation.promise("dinner")["status"] == "kept"
    assert s.details.get("notebook_last_page")["status"] == "planted"
    bad = apply_effects({"set": {"weather": "눈"}}, world=s.world, relation=s.relation, details=s.details, turn=4, source="x")
    assert not bad[0][0] and s.world.flags["weather"] == "비"       # 값 목록 밖의 상태는 받지 않는다


def test_core_event_effects_apply_when_forced_and_duration_keeps_it_ongoing(runs):
    s = overtime()
    s.world.minute = to_min("21:33")
    s.llm.dm = dm(minutes=5)
    s.turn("(창밖을 본다)")                                          # 21:33 → 21:38, rain_starts(21:30~) 강제
    assert s.world.flags["weather"] == "비"
    assert any("효과(이벤트 rain_starts)" in x["text"] for x in s.traces[1]["applied"]["notes"])
    s.world.fired["rehearsal"] = {"turn": 1, "clock": "21:40", "how": ""}
    s.world.minute = to_min("22:05")
    assert "rehearsal" in [e["id"] for e in s.world.ongoing()]        # duration 30분 안
    s.world.minute = to_min("22:15")
    assert "rehearsal" not in [e["id"] for e in s.world.ongoing()]    # 창(~22:50)은 남았지만 30분이 지났다


def test_dm_cannot_fire_event_whose_requires_fail_after_state_changes(runs):
    s = overtime()
    s.relation.stats["trust"]["value"] = 3
    s.world.minute = to_min("20:55")
    s.world.fired["team_chat"] = {"turn": 0, "clock": "20:30", "how": ""}
    ok, msg = s.world.fire("rooftop_ramen", 1, "", gate=s.gate)
    assert ok
    s2 = overtime()
    s2.world.minute = to_min("20:55")
    ok, msg = s2.world.fire("rooftop_ramen", 1, "", gate=s2.gate)   # 신뢰 2: 후보가 아니었던 것을 DM이 골라도 막힌다
    assert not ok and "전제 조건" in msg


def test_charging_is_refused_when_power_is_off(runs):
    s = overtime(dm_out=dm(phone_charging=True, minutes=10))
    s.turn("(충전기를 꽂아 준다)")                                     # 전원 켜짐, 사무실 → 충전된다
    assert s.world.charging and s.world.battery > 24
    s.world.flags["floor_power"] = "꺼짐"
    s.world.charging = False
    before = s.world.battery
    s.turn("(콘센트에 꽂는다)")
    assert not s.world.charging and s.world.battery < before
    assert any(x["text"].startswith("충전 거절") and "14층 전원" in x["text"] for x in s.traces[2]["applied"]["notes"])
    s.world.items["power_bank"]["holder"] = "character"               # 보조배터리를 들고 있으면 된다
    s.turn("(보조배터리를 건넨다)")
    assert s.world.charging


def test_power_off_effect_unplugs_a_charging_phone(runs):
    s = overtime(dm_out=dm(phone_charging=True, minutes=8))
    s.world.minute = to_min("23:00")
    s.world.charging = True
    s.turn("(계속 작업한다)")                                           # 23:00 → 23:08, power_off 강제 → 콘센트 끊김
    assert s.world.flags["floor_power"] == "꺼짐" and not s.world.charging
    assert any("충전이 끊겼다" in x["text"] for x in s.traces[1]["applied"]["notes"])


def test_item_moves_need_both_people_together(runs):
    move = [{"id": "umbrella", "to": "character", "why": "돌려줬다"}]
    s = overtime(dm_out=dm(item_moves=move))
    s.turn("(우산을 내민다) 이거, 늦었죠.")
    assert s.world.items["umbrella"]["holder"] == "character"
    s2 = overtime(dm_out=dm(item_moves=move))
    s2.world.together, s2.world.user_loc = False, "lobby"
    s2.turn("(로비에서 우산을 흔든다)")
    assert s2.world.items["umbrella"]["holder"] == "user"
    assert any("소지품 거절" in x["text"] for x in s2.traces[1]["applied"]["notes"])
    s3 = overtime(dm_out=dm(item_moves=[{"id": "power_bank", "to": "user", "why": "샀다"}]))
    s3.turn("(편의점에 가지 않고 자리에서) 보조배터리 샀어요")          # 편의점에 없는 사람은 집지 못한다
    assert s3.world.items["power_bank"]["holder"] == "store"


def rel_update(**over):
    base = {"stats": [], "stage": None, "address": [], "promises": [], "new_promises": []}
    return {**base, **over}


def test_relationship_updates_are_validated_by_code(runs):
    up = rel_update(
        stats=[{"id": "trust", "delta": 2, "why": "우산을 돌려줬다"},           # ±1을 넘는다
               {"id": "closeness", "delta": 1, "why": ""},                       # 근거가 없다
               {"id": "flutter", "delta": 1, "why": "채원이 바나나우유 농담을 받아쳤다"}],
        stage={"to": "lovers", "why": "고백"},                                   # 두 칸
        address=[{"who": "character", "to": "채원 씨", "why": "같음"}, {"who": "user", "to": "도하야", "why": "채원이 처음 반말로 불렀다"}],
        promises=[{"id": "nope", "status": "kept", "why": "x"}, {"id": "umbrella_back", "status": "kept", "why": "우산을 돌려줬다"}],
        new_promises=[{"by": "도하", "text": "도하가 PT 끝나면 국밥을 사기로 했다."}])
    s = overtime(dm_out=dm(relationship_update=up))
    s.turn("(우산을 돌려주며) 도하야, 이거.")
    notes = [x for x in s.traces[1]["applied"]["notes"] if "관계" in x["text"] or "약속" in x["text"] or "호칭" in x["text"]]
    bad = [x["text"] for x in notes if not x["ok"]]
    r = s.relation
    assert r.value("trust") == 2 and r.value("closeness") == 2 and r.value("flutter") == 2
    assert any("±1" in b for b in bad) and any("근거" in b for b in bad) and any("한 칸씩" in b for b in bad)
    assert any("없는 약속 nope" in b for b in bad)
    assert r.stage == "overtime_mates" and r.address["user"] == "도하야"
    assert r.promise("umbrella_back")["status"] == "kept" and r.promises[-1]["by"] == "도하"
    assert not r.set_promise("umbrella_back", "open", "다시", 2)[0]          # kept/broken은 되돌리지 않는다
    assert not r.change_stat("flutter", 1, "또", 1)[0]                        # 한 턴에 한 번
    # 단계는 needs를 채워야 올라간다
    assert not r.set_stage("some", "가까워졌다", 2)[0]                         # 친밀 2 < 3
    r.stats["closeness"]["value"] = 3
    assert r.set_stage("some", "옥상에서 손을 잡았다", 2)[0] and r.stage == "some"
    assert not r.set_stage("overtime_mates", "", 3)[0]                         # 근거 없이 내리지 않는다
    assert r.log and r.log[-1]["what"] == "stage"


def test_event_effect_counts_toward_the_one_change_per_turn_rule(runs):
    s = overtime()
    s.world.minute, s.world.user_loc, s.world.char_loc = to_min("21:10"), "gukbap", "gukbap"
    s.llm.dm = dm(events=[{"id": "gukbap_together", "how": "국밥이 나온다"}],
                  relationship_update=rel_update(stats=[{"id": "trust", "delta": 1, "why": "밥을 샀다"}]))
    s._retrieve_world = lambda u, n: ([{"id": "gukbap_together"}], [], [], [], [])
    s.turn("(국밥을 받는다)")
    assert s.relation.promise("dinner")["status"] == "kept" and s.relation.value("trust") == 3   # 효과로 +1, DM의 +1은 거절
    assert any("이미 바뀌었다" in x["text"] for x in s.traces[1]["applied"]["notes"] if not x["ok"])


def test_prior_episodes_are_loaded_as_memory_and_planted_details(runs):
    s = overtime()
    hist = load_history("overtime")
    assert [e["id"] for e in s.memory.prior] == [e["id"] for e in hist["episodes"]]
    assert s.memory.session_episodes == []
    prior = [d for d in s.details.items if d.get("episode")]
    assert len(prior) == sum(len(e["details"]) for e in hist["episodes"])
    assert all(d["status"] == "planted" and d["uses"] == [] and d["source"] == "지난 만남" for d in prior)
    assert {"ep1_banana", "ep3_umbrella"} <= {d["id"] for d in s.details.candidates(1)}   # 검색 후보가 된다
    # 회수: DM이 고르고 캐릭터가 쓰면 회수된 것으로 친다. 캐릭터에게는 그때의 원문이 <past>로 간다
    pick = {"need": "", "picks": [{"id": "ep1_banana", "use_as": "둘만 아는 농담"}]}
    s.llm.detail_picks = pick
    s.llm.dm = dm(details=[{"id": "ep1_banana", "mode": "mention", "via": "character", "how": "바나나우유를 내밀며 위로라고 한다"}])
    s.llm.character_out = {**SAY, "used_details": ["ep1_banana"]}
    s.turn("(한숨을 쉰다)")
    doc = s.llm.docs["character"]
    assert "<past from=\"지난 만남: 회식 날 택시\">" in doc and "해장이 아니라 위로" in doc
    assert s.details.recalled("ep1_banana") and not s.details.recalled("ep2_egg")
    # 바뀌지 않는 것이 앞: 지난 만남 → 기억 → 관계 → 지난 턴. 자주 바뀌는 수치는 뒤쪽
    assert doc.index("<past_episodes>") < doc.index("<long_term_memory>") < doc.index("<relationship>") < doc.index("<current_turns>")
    assert doc.index("<closeness") > doc.index("<world_model>") and "<stat " not in doc
    dm_doc = s.llm.docs["drama-manager"]
    assert dm_doc.startswith("<past_episodes>") and "needs 친밀≥3" in dm_doc and 'from="지난 만남: 회식 날 택시"' in dm_doc
    # 세션 안 에피소드 묶기 문서에는 지난 만남이 섞이지 않는다
    assert "<past_episodes>" not in render_episode_doc(s)


def render_episode_doc(s):
    from park import render
    return render.episode_doc(s.memory)


def test_schema_validation_rejects_unknown_vocabulary(runs):
    s = overtime()
    assert validate_schema(s.world, s.relation, s.details) == []
    s.world.data["events"].append({"id": "bad", "where": ["any"], "window": ["21:00", "22:00"], "text": "",
                                   "requires": {"mood": "좋음", "flags": {"weather": "눈"}, "relation": {"min": {"love": 3}}},
                                   "effects": {"teleport": "x", "promise": {"ghost": "kept"}, "plant": ["nope"]}, "duration": -5})
    errors = validate_schema(s.world, s.relation, s.details)
    for frag in ("모르는 전제 조건 mood", "플래그 weather=눈", "관계 수치 love", "모르는 효과 teleport", "약속 ghost", "떡밥 nope", "duration"):
        assert any(frag in e for e in errors), frag
    legacy = session_mod.Session(scenario="hometown", llm=FakeLLM(dm(), SAY))
    legacy.world.data["events"][0]["requires"] = {"relation": {"min": {"trust": 1}}}
    assert any("history.yaml" in e for e in validate_schema(legacy.world, legacy.relation, legacy.details))


def test_usage_is_split_by_llm_label(runs):
    s = overtime()
    entry = s.turn(None)
    by = entry["usage"]["by_label"]
    assert set(by) == {"retrieve-world", "retrieve-details", "extract-facts", "drama-manager", "character"}
    assert by["drama-manager"] == {"count": 1, "input": 1, "cached": 0, "output": 1, "seconds": 0}
    assert s.snapshot()["calls"]["by_label"]["character"]["count"] == 1
    snap = s.snapshot()
    assert snap["relationship"]["stage"] == "overtime_mates" and snap["world"]["flags"] and snap["world"]["items"]


# ---------------- 장기 기억: 오늘 밤의 원문 디테일 ----------------

POSTIT = "다듬으면 채원 씨한테 제일 먼저 보여드릴게요. 약속합니다."


def test_episode_keeps_tonight_originals_as_planted_details_with_their_turns(runs):
    from park import render
    s = overtime()
    s.memory.turns = [{"n": i, "clock": "20:40", "user": f"말 {i}", "narration": None, "reply": f"답 {i}", "channel": "face"}
                      for i in range(1, 10)]
    s.memory.turns[3]["user"] = "(노란 포스트잇을 내민다) 여기다 써 줘요, 약속한다고."
    s.memory.turns[3]["reply"] = f"(포스트잇에 또박또박 적어 건넨다) {POSTIT}"
    s.n = 9
    s.llm.episode_out = {"why": "", "close": True, "until_turn": 5, "title": "포스트잇 약속", "summary": "도하가 약속을 적어 건넸다.",
                         "memories": ["도하는 유저에게 포스트잇을 써 주었다."],
                         "details": [{"id": "postit_promise", "kind": "물건",
                                      "text": f"도하가 유저의 노란 포스트잇에 '{POSTIT}'라고 적어 건넸다.", "affordances": ["약속", "그대로 기억되는 말"]},
                                     {"id": "fake", "kind": "대사", "text": "도하가 '평생 지켜 줄게요'라고 했다.", "affordances": ["약속"]}]}
    s.turn("(장표를 넘긴다)")                                         # T10: 에피소드 묶기
    ep = s.memory.session_episodes[0]
    assert ep["turns"] == [1, 5] and "raw" not in ep and ep["details"] == ["t1_postit_promise"]
    d = s.details.get("t1_postit_promise")
    assert d["status"] == "planted" and d["origin"] == "오늘 밤 T1~T5" and POSTIT in d["seed"] and d["kind"] == "물건"
    assert "t1_postit_promise" in [x["id"] for x in s.details.candidates(11)]   # 지난 만남 디테일처럼 검색 후보가 된다
    notes = [x["text"] for x in s.traces[10]["episode"]["notes"]]
    assert any("인용이 T1~T5 원문에 없다" in x for x in notes)                     # 원문에 없는 인용은 받지 않는다
    assert not any("평생" in x["seed"] for x in s.details.items)
    # 검색기와 DM은 출처로 같은 단어(포스트잇)의 다른 기억을 가른다
    cands = render.details_candidates_xml(s.details, s.details.candidates(11))
    assert 'id="t1_postit_promise"' in cands and 'from="오늘 밤 T1~T5"' in cands and 'from="지난 만남: 회식 날 택시"' in cands
    banana = next(x for x in cands.splitlines() if 'id="banana_desk"' in x)
    assert "from=" not in banana                                       # 시나리오에 미리 심은 떡밥은 출처가 없다
    # 캐릭터는 고른 디테일의 원문을 출처와 함께 받는다
    s.llm.detail_picks = {"need": "", "picks": [{"id": "t1_postit_promise", "use_as": "약속"}]}
    s.llm.dm = dm(details=[{"id": "t1_postit_promise", "mode": "mention", "via": "character", "how": "시험에 원문 그대로 답한다"}])
    s.turn("아까 포스트잇에 뭐라고 썼는지 정확히 말해 봐요.")
    doc = s.llm.docs["character"]
    assert '<past from="오늘 밤 T1~T5">' in doc and POSTIT in doc
    assert 'from="오늘 밤 T1~T5"' in s.llm.docs["drama-manager"]


def test_episode_prompt_and_character_rules_ask_for_originals_and_no_invention(runs):
    s = overtime()
    assert "따옴표째" in s.prompt("episode") and "details" in s.prompt("episode")
    ch = s.prompt("character")
    assert "지어내지 않는다" in ch and "<past>" in ch


# ---------------- 잠긴 비밀 ----------------

class GuardLLM(FakeLLM):
    """캐릭터가 처음엔 first를 내고, 다시 쓰게 하면 rewrites를 차례로 낸다."""
    def __init__(self, dm, first, rewrites):
        super().__init__(dm, first)
        self.rewrites = list(rewrites)

    def json(self, system, user, schema, *, label, effort="low"):
        if label == "character-guard":
            self.labels.append(label)
            self.docs.setdefault("guard", []).append(user)
            self.calls.append({"label": label, "input": 1, "cached": 0, "output": 1, "seconds": 0})
            return {**SAY, **self.rewrites.pop(0)}
        return super().json(system, user, schema, label=label, effort=effort)


LEAK = {**SAY, "say": "이 문장, 채원 씨가 한 말에서 시작했거든요.", "revealed": ["main_copy"]}


def test_goal_after_secret_is_hidden_while_the_secret_is_locked(runs):
    from park import render
    s = overtime()
    doc = lambda: render.characteristic_for_character(s.character, "")
    tell = s.character.data["goals"][2]
    assert tell["id"] == "tell_copy" and tell["after_secret"] == "main_copy"
    assert tell["text"] not in doc() and "final_copy" not in doc() and "메인 카피를 확정" in doc()
    s.character.open_secret("main_copy", 3)
    assert tell["text"] in doc()
    # 카피 문구는 공개 정보로 늘 보이고, 비밀 fact에는 출처만 있다
    secret = s.character.secret("main_copy")
    assert "퇴근길이 제일 긴 하루" not in secret["fact"] and any("퇴근길이 제일 긴 하루" in m for m in s.memory.long_term)
    s.character.data["goals"].append({"id": "x", "text": "", "status": "open", "after_secret": "nope"})
    assert any("없는 비밀 nope" in e for e in validate_schema(s.world, s.relation, s.details, s.character))


def test_locked_secret_revealed_by_character_is_rewritten_by_cover(runs):
    s = overtime(llm=GuardLLM(dm(), LEAK, [{"say": "(볼펜을 딸깍인다) 카피는 원래 새벽에 나와요.", "revealed": []}]))
    entry = s.turn("(우산을 가방에 넣는다) 이건 다음에 줄래요.")
    assert entry["reply"] == "(볼펜을 딸깍인다) 카피는 원래 새벽에 나와요."
    assert s.character.secret("main_copy")["status"] == "locked"
    assert "<rewrite>" in s.llm.docs["guard"][0] and s.character.secret("main_copy")["cover"] in s.llm.docs["guard"][0]
    texts = [x["text"] for x in s.traces[1]["character_applied"]]
    assert any("잠긴 비밀(main_copy)을 털어놓아 다시 쓰게 함" in x for x in texts) and any("털어놓지 않게 다시 씀" in x for x in texts)
    # 두 번 다시 써도 털어놓으면 대사는 쓰되 비밀은 잠근 채 둔다
    s2 = overtime(llm=GuardLLM(dm(), LEAK, [LEAK, LEAK]))
    entry = s2.turn("(우산을 가방에 넣는다)")
    assert entry["reply"] == LEAK["say"] and s2.llm.labels.count("character-guard") == 2
    assert s2.character.secret("main_copy")["status"] == "locked"
    assert any("비밀은 잠근 채 둠" in x["text"] for x in s2.traces[1]["character_applied"])
    # DM이 연 비밀은 털어놓을 수 있다
    s3 = overtime(llm=GuardLLM(dm(reveal=[{"id": "main_copy", "why": "노트"}]), LEAK, []))
    s3.turn("(노트를 본다)")
    assert s3.character.secret("main_copy")["status"] == "revealed" and "character-guard" not in s3.llm.labels


def test_dm_prompt_forbids_locked_secret_even_as_negation(runs):
    assert "부정문으로도 쓰지 않는다" in overtime().prompt("dm")
    assert "locked인 비밀이 목표보다 앞선다" in overtime().prompt("character")


def test_detail_planted_by_event_is_not_a_candidate_before_that_event(runs):
    s = overtime()
    assert "notebook_last_page" not in [d["id"] for d in s.details.candidates(1)]
    apply_effects(s.world.event("notebook_open")["effects"], world=s.world, relation=s.relation, details=s.details,
                  turn=5, source="이벤트 notebook_open")
    assert s.details.get("notebook_last_page")["status"] == "planted"
    assert "notebook_last_page" in [d["id"] for d in s.details.candidates(9)]
    s.details.get("hoarse_voice")["planted_by"] = "rain_starts"                 # 그 이벤트가 plant하지 않으면 오류
    assert any("hoarse_voice" in e for e in validate_schema(s.world, s.relation, s.details, s.character))


# ---------------- 핵심 이벤트와 지문, 시간 상한 ----------------

RAIN = "창밖으로 비가 쏟아지기 시작한다"


def test_forced_core_event_text_is_added_to_that_turns_narration(runs):
    s = overtime(dm_out=dm(minutes=60, narration="한 시간 사이 출력물이 쌓인다."))
    s.world.minute = to_min("20:40")
    entry = s.turn("(둘이 말없이 한 시간쯤 장표를 고친다)")
    assert s.world.clock == "21:40" and s.world.flags["weather"] == "비"
    assert entry["narration"].startswith("한 시간 사이 출력물이 쌓인다.") and RAIN in entry["narration"]
    assert RAIN in s.llm.docs["character"]                             # 캐릭터도 그 지문을 본다
    note = next(x["text"] for x in s.traces[1]["applied"]["notes"] if "핵심 이벤트 rain_starts" in x["text"])
    assert "지문에 붙였다" in note and "21:30~21:50" in note and "시작 5분 경과" not in note
    s2 = overtime(dm_out=dm(minutes=60))                                # DM 지문이 없으면 이벤트 문장이 지문이 된다
    s2.world.minute = to_min("20:40")
    assert RAIN in s2.turn("(작업한다)")["narration"]


def test_core_event_fired_by_dm_without_narration_uses_its_how(runs):
    s = overtime(dm_out=dm(minutes=5, events=[{"id": "rain_starts", "how": "유리창에 빗방울이 번진다."}]))
    s.world.minute = to_min("21:28")
    assert s.turn("(창밖을 본다)")["narration"] == "유리창에 빗방울이 번진다."


def test_core_events_skip_the_retriever_and_reach_the_dm(runs):
    s = overtime()
    s.world.minute = to_min("21:20")
    picks = s._retrieve_world("(작업한다)", 1)[0]                     # 가짜 검색기는 아무것도 고르지 않는다
    assert {"id": "rain_starts", "core": True} in picks
    s.world.minute = to_min("20:30")
    assert all(p["id"] != "power_off" for p in s._retrieve_world("(작업한다)", 1)[0])   # 시간 창이 멀면 넣지 않는다
    s.world.minute = to_min("21:20")
    s.llm.dm = dm(minutes=2)
    s.turn("(작업한다)")
    assert 'id="rain_starts"' in s.llm.docs["drama-manager"].split("<candidates>")[1] and 'core="yes"' in s.llm.docs["drama-manager"]
    assert "개수에 세지 않는다" in s.prompt("dm")


def test_time_cap_is_reported_with_the_minutes_that_really_passed(runs):
    s = overtime(dm_out=dm(minutes=120))
    s.world.minute = to_min("20:42")
    s.turn("(두 시간 작업한다)")
    assert s.world.clock == "22:42" and s.traces[1]["applied"]["minutes"] == 120   # 120분은 자르지 않는다
    s.llm.dm = dm(minutes=200)
    s.world.minute = to_min("20:00")
    s.turn("(오래 작업한다)")
    notes = [x["text"] for x in s.traces[2]["applied"]["notes"]]
    assert s.world.clock == "23:00" and any("DM +200분 → +180분" in x for x in notes) and any("(+180분" in x for x in notes)
    s.llm.dm = dm(minutes=90)
    s.turn("(더 작업한다)")                                            # 끝 시각(23:59)에서 멈춘다
    assert s.world.clock == "23:59" and s.traces[3]["applied"]["minutes"] == 59
    assert "180분까지" in s.prompt("dm")
