"""코드로 지키는 규칙과 턴 흐름. LLM은 가짜로 바꾼다."""
import random

import pytest

from park import session as session_mod
from park.state import Character, Details, Memory, World, to_min


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


def test_facts_can_be_retired():
    w = World.fresh()
    fid = w.add_fact("언덕에 남은 벤치는 하나뿐이다.", "수아", 3, "hill")
    assert [f["id"] for f in w.active_facts()] == [fid]
    assert w.retire_fact(fid, 7)[0] and w.active_facts() == [] and not w.retire_fact(fid, 8)[0]


def test_character_update_touches_dynamic_only():
    c = Character.fresh()
    core = dict(c.data["core"])
    c.update({"mood": "서운함", "attitude": None, "want": None, "grievances": [{"id": "always_waiting", "intensity": 9}],
              "new_grievance": None, "consistency": ""}, 1)
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
            return {"picks": [], "facts": [{"id": f, "why": ""} for f in self.facts]}
        if label == "extract-facts":
            return {"items": []}
        if label == "retrieve-details":
            return {"need": "", "picks": []}
        if label == "drama-manager":
            return dict(self.dm)
        if label == "character":
            return dict(self.character_out)
        if label == "dm-episode":
            return {"why": "", "close": False, "until_turn": 0, "title": "", "summary": "", "memories": []}
        raise AssertionError(label)


def dm(**over):
    base = {"scene_read": "", "minutes": 2, "minutes_why": "", "user_location": None, "character_location": None,
            "together": True, "channel": "face", "character_phone_use": "idle", "offscreen": None, "beat": "setup",
            "tension": 2, "events": [], "details": [], "reveal": [], "directive": "",
            "character_update": {"mood": None, "attitude": None, "want": None, "grievances": [], "new_grievance": None, "consistency": ""},
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


def test_scene_fact_from_suah_is_kept_and_retrieved_not_broadcast(runs):
    add = {"source": "수아", "kind": "fact", "name": "", "text": "언덕에 남은 벤치는 하나뿐이다.", "near": "hill",
           "accept": True, "why": "", "event_text": "", "affordances": []}
    llm = FakeLLM(dm(world_additions=[add]), SAY)
    s = session_mod.Session(llm=llm)
    s.turn("저기 비었네")
    fid = s.world.facts[0]["id"]
    assert "남은 벤치는 하나뿐" in llm.docs["character"]              # 이번 턴에 새로 생긴 사실은 바로 보인다
    llm.dm = dm()
    s.turn("앉자")
    assert "남은 벤치는 하나뿐" not in llm.docs["drama-manager"]       # 검색되지 않으면 DM에게도 가지 않는다
    assert "남은 벤치는 하나뿐" not in llm.docs["character"]
    llm.facts, llm.dm = [fid], dm(show_facts=[fid], retire_facts=[{"id": fid, "why": "둘이 앉았다"}])
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
