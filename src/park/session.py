"""한 턴의 흐름. 유저 입력 → 검색기 둘(병렬) → 드라마 매니저 → 코드 검증과 반영 → 캐릭터 → (10턴마다) 에피소드 정리.

드라마 매니저가 캐릭터보다 먼저 판단하므로, 캐릭터는 이번 턴에 바뀐 세계를 보고 말한다.
턴의 모든 판단은 trace로 남고, emit으로 화면에 단계마다 흘려보낸다.
"""
from __future__ import annotations

import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import render
from .llm import LLM, ROOT
from .state import (BEATS, DEFAULT_SCENARIO, MAX_ADVANCE, PHONE_USES, Character, Details, Memory, Relationship, World,
                    apply_effects, fill, josa, load_history, requires_ok, validate_schema)

PROMPTS = Path(__file__).resolve().parent / "prompts"
EPISODE_EVERY = 10
# 출력 가드레일. 싼 모델은 "한국어로만"이라고 써 둬도 일본어·한자·깨진 기호로 넘어간다(도쿄 맥락이 쌓이면 특히).
# 프롬프트로는 못 막으니 코드가 본다: 가나, 한자, CJK 문장부호(、。「」【】), 수학 기호류(⟂)
FOREIGN = re.compile(r"[　-〿぀-ヿㇰ-ㇿ一-鿿｡-ﾟ⟀-⟯⦀-⧿]+")
LANG_RETRIES = 2   # 대사 다시 쓰기 최대 횟수(다른 언어, 잠긴 비밀 누설 공통)


def foreign(text: str | None) -> str:
    """한국어 대사에 섞이면 안 되는 글자들(중복 없이). 없으면 빈 문자열."""
    return "".join(dict.fromkeys("".join(FOREIGN.findall(text or ""))))


def strip_foreign(text: str) -> str:
    return re.sub(r"\s{2,}", " ", FOREIGN.sub("", text)).strip()
DM_EFFORT = os.environ.get("RP_DM_EFFORT", "low")  # medium은 판단이 조금 낫지만 턴당 10~30초 걸린다


def prompt(name: str, values: dict) -> str:
    """프롬프트는 이야기와 상관없이 하나다. 캐릭터 이름, 무대, 흐름은 시나리오 값으로 채운다."""
    return fill((PROMPTS / f"{name}.md").read_text(encoding="utf-8"), values)


# ---------------- 스키마 ----------------

def obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def enum(values: list[str]) -> dict:
    return {"type": "string", "enum": values or ["none"]}


def nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


S = {"type": "string"}
SN = nullable(S)


def world_pick_schema(event_ids):
    # fact id는 대화 중에 늘어나므로 enum으로 묶지 않는다(스키마가 매 턴 바뀌면 캐시가 깨진다). 후보에 있는지는 코드가 본다
    return obj({"picks": {"type": "array", "items": obj({"id": enum(event_ids)})},
                "facts": {"type": "array", "items": obj({"id": S})}})


def detail_pick_schema(ids):
    return obj({"need": S, "picks": {"type": "array", "items": obj({"id": enum(ids), "use_as": S})}})


def relation_schema(name, stat_ids, stage_ids):
    # 약속 id는 대화 중에 늘어나므로 enum으로 묶지 않는다. 있는 약속인지는 코드가 본다
    return obj({
        "stats": {"type": "array", "items": obj({"id": enum(stat_ids), "delta": {"type": "integer"}, "why": S})},
        "stage": nullable(obj({"to": enum(stage_ids), "why": S})),
        "address": {"type": "array", "items": obj({"who": enum(["character", "user"]), "to": S, "why": S})},
        "promises": {"type": "array", "items": obj({"id": S, "status": enum(["kept", "broken"]), "why": S})},
        "new_promises": {"type": "array", "items": obj({"by": enum(["유저", name]), "text": S})},
    })


def dm_schema(*, name, locations, event_ids, detail_ids, secret_ids, grievance_ids, goal_ids, item_ids=(), relation=None):
    extra = {}
    if item_ids:   # 소지품과 관계가 있는 이야기에서만 더한다(없는 이야기는 예전 스키마 그대로)
        extra["item_moves"] = {"type": "array", "items": obj({"id": enum(list(item_ids)), "to": enum(["user", "character", *locations]), "why": S})}
    if relation:
        extra["relationship_update"] = relation_schema(name, list(relation.stats), [x["id"] for x in relation.stages])
    return obj({
        "scene_read": S,
        "minutes": {"type": "integer"},
        "minutes_why": S,
        "user_location": nullable(enum(locations)),
        "character_location": nullable(enum(locations)),
        "together": {"type": "boolean"},
        "channel": enum(["face", "phone", "none"]),
        "character_phone_use": enum(PHONE_USES),
        "phone_charging": {"type": "boolean"},
        "offscreen": SN,
        "beat": enum(BEATS),
        "tension": {"type": "integer"},
        "events": {"type": "array", "items": obj({"id": enum(event_ids), "how": S})},
        "details": {"type": "array", "items": obj({"id": enum(detail_ids), "mode": enum(["plant", "mention", "payoff"]),
                                                   "via": enum(["character", "narration"]), "how": S})},
        "reveal": {"type": "array", "items": obj({"id": enum(secret_ids), "why": S})},
        "directive": S,
        "character_update": obj({
            "mood": SN, "attitude": SN, "want": SN,
            "grievances": {"type": "array", "items": obj({"id": enum(grievance_ids), "intensity": {"type": "integer"}, "why": S})},
            "new_grievance": SN}),
        "goal_updates": {"type": "array", "items": obj({"id": enum(goal_ids), "status": enum(["open", "done", "failed", "dropped"])})},
        "world_additions": {"type": "array", "items": obj({
            "source": enum(["유저", name]), "kind": enum(["location", "fact", "past"]), "name": S, "text": S,
            "near": nullable(enum(locations)), "arrive": enum(["both", "user", "character", "none"]),
            "accept": {"type": "boolean"}, "why": S,
            "event_text": S, "affordances": {"type": "array", "items": S}})},
        "show_facts": {"type": "array", "items": S},
        "retire_facts": {"type": "array", "items": obj({"id": S})},
        "user_facts": {"type": "array", "items": obj({"text": S, "affordances": {"type": "array", "items": S}})},
        "narration": SN,
        **extra,
    })


def character_schema(locations, secret_ids):
    return obj({"say": S, "move": nullable(obj({"to": enum(locations), "alone": {"type": "boolean"}})), "phone_use": enum(PHONE_USES),
                "used_details": {"type": "array", "items": S}, "revealed": {"type": "array", "items": enum(secret_ids)}})


def extract_schema(locations, name):
    return obj({"items": {"type": "array", "items": obj({
        "source": enum(["유저", name]), "kind": enum(["location", "fact", "past"]), "name": S, "text": S,
        "near": nullable(enum(locations))})}})


# details는 history.yaml의 episode details와 같은 형식(id, kind, text, affordances). 요약에서 사라질 원문을 남긴다
EPISODE_SCHEMA = obj({"why": S, "close": {"type": "boolean"}, "until_turn": {"type": "integer"}, "title": S, "summary": S,
                      "details": {"type": "array", "items": obj({"id": S, "kind": S, "text": S,
                                                                 "affordances": {"type": "array", "items": S}})},
                      "memories": {"type": "array", "items": S}})


def usage(calls: list[dict], by_label: bool = True) -> dict:
    """호출 합계. by_label이면 호출 label(검색기, DM, 캐릭터 등)마다의 합계도 함께."""
    out = {"count": len(calls), "input": sum(c["input"] for c in calls), "cached": sum(c["cached"] for c in calls),
           "output": sum(c["output"] for c in calls), "seconds": round(sum(c["seconds"] for c in calls), 1)}
    if by_label:
        labels = list(dict.fromkeys(c["label"] for c in calls))
        out["by_label"] = {lb: usage([c for c in calls if c["label"] == lb], by_label=False) for lb in labels}
    return out


# ---------------- 세션 ----------------

class Session:
    def __init__(self, *, scenario: str = DEFAULT_SCENARIO, baseline: bool = False, seed: int | None = None,
                 llm: LLM | None = None, tag: str | None = None) -> None:
        self.llm = llm or LLM()
        self.scenario = scenario
        self.character = Character.fresh(scenario)
        self.world = World.fresh(scenario, self.character.name)
        history = load_history(scenario)   # 지난 만남과 관계 상태(없는 이야기는 None)
        self.relation = Relationship.fresh(history) if history else None
        self.details = Details.fresh(scenario, history)
        self.memory = Memory.fresh(self.character, history)
        if errors := validate_schema(self.world, self.relation, self.details, self.character):
            raise ValueError(f"{scenario} 스키마 오류: " + "; ".join(errors))
        self.rng = random.Random(seed)
        self.n = 0
        self.proposal: str | None = None   # 캐릭터가 같이 가자고 한 장소. 다음 턴 드라마 매니저가 유저가 따라왔는지 판단한다
        self.tension_log: list[dict] = []
        self.log: list[dict] = []          # 화면용 전체 대화. {n, clock, user, narration, reply, channel, baseline}
        self.traces: dict[int, dict] = {}
        self.baseline_on = baseline
        self.baseline_messages: list[dict] = []
        self.started = False
        stamp = datetime.now().strftime("%m%d_%H%M%S")
        self.record = ROOT / "runs" / "park" / f"{stamp}_{scenario}{'_' + tag if tag else ''}.jsonl"
        self.record.parent.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return self.character.name

    def prompt(self, name: str) -> str:
        c, sc = self.character.data, self.world.scenario
        values = {"name": self.name, "place": sc["place"], "setting": sc["setting"], "arc": sc["arc"].strip(),
                  "profile": c["profile"], "traits": " ".join(c["core"]["traits"]), "speech": c["core"]["speech"]}
        # 지난 만남·관계·플래그·소지품 안내는 그런 상태가 있는 이야기에서만 붙인다. 없는 이야기의 프롬프트는 예전 그대로다
        guides = {"dm": ["dm_items" if self.world.items else "", "dm_relation" if self.relation else ""],
                  "character": ["character_relation" if self.relation else "", "character_items" if self.world.items or self.world.flags else ""]}
        values["state_guide"] = "\n\n".join(prompt(g, values) for g in guides.get(name, []) if g)
        return prompt(name, values)

    def gate(self, e: dict) -> tuple[bool, str]:
        """이벤트의 전제 조건(requires)을 지금 상태에 대어 본다."""
        return requires_ok(e.get("requires"), world=self.world, relation=self.relation, details=self.details)

    # CLI가 턴 사이에 세션을 파일로 들고 있도록. LLM 클라이언트는 빼고, 호출 기록만 남긴다
    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        state["_calls"] = self.llm.calls
        del state["llm"]
        return state

    def __setstate__(self, state: dict) -> None:
        calls = state.pop("_calls", [])
        state.setdefault("relation", None)   # 관계 상태를 넣기 전에 저장된 세션
        self.__dict__.update(state)
        self.llm = LLM()
        self.llm.calls = calls

    # ---- 한 턴 ----
    def turn(self, user_text: str | None, emit: Callable[[dict], None] = lambda e: None) -> dict:
        self.n += 1
        n = self.n
        user_input = user_text or self.world.scenario["opening"]
        self.started = True
        call_start = len(self.llm.calls)
        trace: dict = {"n": n, "clock_before": self.world.clock, "user": user_input, "notes": []}
        self.traces[n] = trace
        emit({"type": "user", "n": n, "text": user_input, "clock": self.world.clock})

        with ThreadPoolExecutor(max_workers=4) as pool:
            baseline = pool.submit(self._baseline, user_input) if self.baseline_on else None
            world_future = pool.submit(self._retrieve_world, user_input, n)
            det_future = pool.submit(self._retrieve_details, user_input, n)
            ext_future = pool.submit(self._extract, user_input, n)
            ev_picks, fact_picks, ev_cands, fact_cands, ev_blocked = world_future.result()
            det_picks = det_future.result()
            extracted = ext_future.result()
            trace["retrieval"] = {"event_candidates": [e["id"] for e in ev_cands], "event_blocked": ev_blocked, "events": ev_picks,
                                  "fact_candidates": [f["id"] for f in fact_cands], "facts": fact_picks, "details": det_picks,
                                  "extracted": extracted}
            emit({"type": "retrieval", "n": n, **trace["retrieval"]})

            dm = self._dm(user_input, n, ev_picks, fact_picks, det_picks, extracted)
            trace["dm"] = dm
            emit({"type": "dm", "n": n, "dm": dm})

            applied = self._apply_dm(dm, n, ev_picks, fact_picks, det_picks, opening=user_text is None)
            trace["applied"] = applied
            emit({"type": "applied", "n": n, **applied, "world": self.world.snapshot()})

            now = {"n": n, "clock": self.world.clock, "user": user_input, "channel": dm["channel"],
                   "narration": applied["narration"]}
            reply = None
            if dm["channel"] != "none":
                out = self._character(now, dm, applied["picked_details"], applied["fired_now"], applied["shown_facts"])
                trace["character"] = out
                reply = re.sub(r"\s*\n+\s*", " ", out["say"]).strip()
                trace["character_applied"] = out.get("_guard", []) + self._apply_character(out, n, applied["picked_details"])
            else:
                trace["character_applied"] = [{"ok": False, "text": f"떡밥 안 쓰임({self.name}에게 닿지 않음): {d['id']}"}
                                              for d in applied["picked_details"]]
            base_reply = baseline.result() if baseline else None

        entry = {**now, "reply": reply, "baseline": base_reply}
        self.memory.turns.append({k: entry[k] for k in ("n", "clock", "user", "narration", "reply", "channel")})
        self.log.append(entry)
        self.tension_log.append({"n": n, "beat": dm["beat"], "tension": dm["tension"]})
        emit({"type": "reply", "n": n, "reply": reply, "baseline": base_reply, "narration": applied["narration"],
              "character": trace.get("character"), "character_applied": trace.get("character_applied")})

        if n % EPISODE_EVERY == 0 and len(self.memory.turns) > self.memory.keep_raw:
            trace["episode"] = self._episode()
            emit({"type": "episode", "n": n, **trace["episode"]})

        trace["calls"] = self.llm.calls[call_start:]
        entry["usage"] = usage([c for c in trace["calls"] if c["label"] != "baseline"])
        entry["baseline_usage"] = usage([c for c in trace["calls"] if c["label"] == "baseline"])
        trace["clock_after"] = self.world.clock
        emit({"type": "done", "n": n, "calls": trace["calls"], "state": self.snapshot()})
        with self.record.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"entry": entry, "trace": trace, "state": self.snapshot()}, ensure_ascii=False, default=str) + "\n")
        return entry

    # ---- 검색기 ----
    def _retrieve_world(self, user_input: str, n: int):
        """월드 모델에서 지금 필요한 조각을 고른다. 이벤트는 코드가 장소·시간 창·전제 조건(requires)으로 먼저 거르고,
        장면 사실은 유효한 것 전부가 후보다. 전제 조건을 못 넘은 이벤트는 검색기에 가지 않고 이유와 함께 trace에 남는다."""
        blocked: list[dict] = []
        ev_cands = self.world.event_candidates(horizon=60, gate=self.gate, blocked=blocked)
        fact_cands = self.world.active_facts()
        if not ev_cands and not fact_cands:
            return [], [], [], [], blocked
        doc = render.situation_xml(self.world, self.memory, user_input, n) + "\n" + render.world_candidates_xml(self.world, ev_cands, fact_cands)
        out = self.llm.json(self.prompt("retrieve_world"), doc, world_pick_schema(self._all_event_ids()), label="retrieve-world")
        ev_ids, fact_ids = {e["id"] for e in ev_cands}, {f["id"] for f in fact_cands}
        picks = [p for p in out["picks"] if p["id"] in ev_ids][:4]
        # 핵심 이벤트는 검색기를 거치지 않고 늘 DM 후보에 든다(개수 제한에도 세지 않는다)
        picks += [{"id": e["id"], "core": True} for e in ev_cands if e.get("core") and e["id"] not in {p["id"] for p in picks}]
        return picks, [p for p in out["facts"] if p["id"] in fact_ids][:6], ev_cands, fact_cands, blocked

    def _retrieve_details(self, user_input: str, n: int):
        cands = self.details.candidates(n)
        if not cands:
            return {"need": "", "picks": []}
        doc = render.situation_xml(self.world, self.memory, user_input, n) + "\n" + render.details_candidates_xml(self.details, cands)
        out = self.llm.json(self.prompt("retrieve_details"), doc, detail_pick_schema(self._all_detail_ids()), label="retrieve-details")
        ids = {d["id"] for d in cands}
        out["picks"] = [p for p in out["picks"] if p["id"] in ids][:4]
        # 쓸모는 그 떡밥의 affordances 중 하나만 받는다. 검색기가 지어낸 해석이 DM을 끌고 가지 않게 한다
        for p in out["picks"]:
            if p["use_as"] not in self.details.get(p["id"])["affordances"]:
                p["use_as"] = ""
        return out

    # ---- 장면 사실 추출. 지난 턴 캐릭터의 말과 이번 유저 입력에서 새로 생긴 것을 뽑는다. 받을지는 DM이 판단한다 ----
    def _extract(self, user_input: str, n: int) -> list[dict]:
        last = self.memory.turns[-1] if self.memory.turns else None
        doc = "\n".join([
            render.situation_xml(self.world, self.memory, user_input, n),
            render.tag("places", render.t(", ".join(f"{k}={v['name']}" for k, v in self.world.locations.items()))),
            render.tag("last_reply", render.t(last["reply"] if last and last.get("reply") else "(없음)")),
        ])
        out = self.llm.json(self.prompt("extract_facts"), doc, extract_schema(list(self.world.locations), self.name), label="extract-facts")
        return out["items"]

    # 스키마의 enum은 전체 id로 고정한다(매 턴 바뀌면 프롬프트 캐시가 깨진다). 후보에 들었는지는 코드가 확인한다
    def _all_event_ids(self) -> list[str]:
        return [e["id"] for e in self.world.data["events"]]

    def _all_detail_ids(self) -> list[str]:
        return [d["id"] for d in self.details.items]

    # ---- 드라마 매니저 ----
    def _dm(self, user_input: str, n: int, ev_picks: list, fact_picks: list, det_picks: dict, extracted: list) -> dict:
        doc = render.dm_doc(world=self.world, details=self.details, character=self.character, memory=self.memory,
                            user_input=user_input, n=n, ev_picks=ev_picks, fact_picks=fact_picks, det_picks=det_picks,
                            extracted=extracted,
                            tension_log=self.tension_log,
                            proposal=self.proposal, relation=self.relation)
        c = self.character.data
        schema = dm_schema(
            name=self.name, locations=list(self.world.locations), event_ids=self._all_event_ids(),
            detail_ids=self._all_detail_ids(), secret_ids=[s["id"] for s in c["secrets"]],
            grievance_ids=[g["id"] for g in c["dynamic"]["grievances"]], goal_ids=[g["id"] for g in c["goals"]],
            item_ids=list(self.world.items), relation=self.relation)
        out = self.llm.json(self.prompt("dm"), doc, schema, label="drama-manager", effort=DM_EFFORT)
        self.traces[n]["dm_doc"] = doc
        return out

    def _apply_dm(self, dm: dict, n: int, ev_picks: list, fact_picks: list, det_picks: dict, *, opening: bool = False) -> dict:
        """드라마 매니저의 판단을 세계에 반영한다. 규칙에 어긋나는 것은 거절하고 이유를 남긴다."""
        w, notes = self.world, []

        def note(ok_msg):
            ok, msg = ok_msg
            if msg:
                notes.append({"ok": ok, "text": msg})
            return ok

        # 가드레일: 유저에게 보이는 지문과 캐릭터에게 넘어가는 속마음에서 다른 언어를 지운다(속마음이 캐릭터를 그 언어로 끌고 간다)
        for key, label in (("narration", "지문"), ("directive", "속마음")):
            if bad := foreign(dm.get(key)):
                cleaned = strip_foreign(dm[key])
                dm[key] = cleaned if cleaned or key != "narration" else None   # 지문이 다 지워지면 없는 것으로
                notes.append({"ok": False, "text": f"가드레일: {label}에서 한국어가 아닌 글자({bad[:12]})를 지움"})
        # 시간과 배터리. 시작 턴은 도착 시각이 이미 반영돼 있으므로 흐르지 않는다
        if opening and dm["minutes"]:
            notes.append({"ok": False, "text": f"시작 턴이라 +{dm['minutes']}분 → 0분 (도착 시각 {w.clock}이 이미 반영됨)"})
            dm["minutes"], dm["minutes_why"] = 0, "시작 장면"
        before, start_min = w.clock, w.minute
        if dm["phone_charging"] and not w.charging:
            ok, why = w.can_charge(self.relation, self.details)
            if not ok:   # 월드 상태와 맞지 않는 충전(전원이 내려간 콘센트 등)은 코드가 거절한다
                notes.append({"ok": False, "text": f"충전 거절: {why}"})
                dm["phone_charging"] = False
        if dm["phone_charging"] != w.charging:
            notes.append({"ok": True, "text": f"{self.name}의 휴대폰 충전 " + ("시작" if dm["phone_charging"] else "끝")})
        w.charging = dm["phone_charging"]
        for msg in w.advance(dm["minutes"], dm["character_phone_use"]):
            notes.append({"ok": True, "text": msg})
        spent = w.minute - start_min   # 상한(MAX_ADVANCE)과 끝 시각 때문에 DM이 낸 분보다 짧을 수 있다
        if spent != dm["minutes"]:
            notes.append({"ok": False, "text": f"시간 고침: DM +{dm['minutes']}분 → +{spent}분 (한 턴 최대 {MAX_ADVANCE}분, 끝 {w.data['end']})"})
        notes.append({"ok": True, "text": f"시간 {before} → {w.clock} (+{spent}분, {dm['minutes_why']})"})
        # 위치. 함께 있던 캐릭터를 드라마 매니저가 혼자 떠나보내지는 않는다(혼자 자리를 뜰지는 캐릭터가 정한다)
        was_together = w.together
        note(w.move("user", dm["user_location"]))
        char_to = dm["character_location"]
        if was_together and char_to and char_to != w.char_loc and char_to != w.user_loc:
            notes.append({"ok": False, "text": f"{self.name} 이동 거절: 함께 있던 {josa(self.name, '을/를')} 혼자 {josa(w.loc_name(char_to), '으로/로')} 보낼 수는 없다({josa(self.name, '이/가')} 정한다)"})
            char_to = None
        note(w.move("character", char_to))
        w.together = dm["together"]
        if w.char_loc != w.user_loc and w.together:
            notes.append({"ok": False, "text": "함께 있다고 했지만 위치가 달라 떨어져 있는 것으로 고쳤다"})
            w.together = False
        if was_together and not w.together and w.char_loc == w.user_loc:
            notes.append({"ok": False, "text": f"떨어졌다고 했지만 둘 다 {w.loc_name(w.user_loc)}에 그대로다(같은 장소 안에서 떨어진 것이 아니라면 이동이 빠졌다)"})
        if dm["channel"] == "phone" and not w.phone_on:
            notes.append({"ok": False, "text": f"{self.name}의 휴대폰이 꺼져 있어 전화/메시지가 닿지 않는다(channel phone → none)"})
            dm["channel"] = "none"
        # 곁에서 말하고 떠나는 중이면 캐릭터는 들었다. 처음부터 떨어져 있었을 때만 마주 들을 수 없다
        if dm["channel"] == "face" and not w.together and not was_together:
            dm["channel"] = "phone" if w.phone_on else "none"
            notes.append({"ok": False, "text": f"떨어져 있어 channel face → {dm['channel']}"})
        # 소지품. 건네기는 함께 있을 때만, 장소에 놓인 것은 그 장소에 있는 사람만 집는다
        for m in dm.get("item_moves") or []:
            note(w.give(m["id"], m["to"]))
        # 이벤트. 일어나는 순간 전제 조건을 다시 보고, 일어나면 효과를 반영한다
        # 핵심 이벤트는 유저 화면에 꼭 나와야 하므로, 지문이 없으면 DM의 how를, 코드가 강제했으면 이벤트 문장을 지문에 붙인다
        valid_ev = {p["id"] for p in ev_picks}
        fired_now, narration = [], dm["narration"]
        for e in dm["events"]:
            if e["id"] not in valid_ev:
                notes.append({"ok": False, "text": f"이벤트 거절: {e['id']}는 이번 후보가 아니다"})
            elif note(w.fire(e["id"], n, e["how"], gate=self.gate)):
                fired_now.append({"id": e["id"], "how": e["how"]})
                self._effects(e["id"], n, notes)
                if w.event(e["id"]).get("core") and not narration:
                    narration = e["how"]
                    notes.append({"ok": True, "text": f"핵심 이벤트 {e['id']}: 지문이 없어 how를 지문으로 씀"})
        for e in w.forced_core(n, gate=self.gate):
            fired_now.append({"id": e["id"], "how": e["text"]})
            narration = f"{narration} {e['text']}" if narration else e["text"]
            notes.append({"ok": True, "text": f"핵심 이벤트 {e['id']}를 코드가 일으켜 지문에 붙였다"
                                              f"(시간 창 {e['window'][0]}~{e['window'][1]}이 열리고 5분이 지나도 DM이 일으키지 않음)"})
            self._effects(e["id"], n, notes)
        if w.charging and not (ok_why := w.can_charge(self.relation, self.details))[0]:
            w.charging = False
            notes.append({"ok": False, "text": f"충전이 끊겼다: {ok_why[1]}"})
        # 떡밥. 고른 것은 시도로만 남기고, 지문에 담았으면 바로, 캐릭터에게 맡겼으면 캐릭터가 실제로 쓴 뒤에 확정한다
        valid_det = {p["id"] for p in det_picks.get("picks", [])}
        picked = []
        for d in dm["details"]:
            if d["id"] not in valid_det:
                notes.append({"ok": False, "text": f"떡밥 거절: {d['id']}는 이번 후보가 아니다"})
                continue
            ok, msg, mode = self.details.attempt(d["id"], d["mode"], d["how"], n)
            notes.append({"ok": ok, "text": msg})
            if not ok:
                continue
            if d["via"] == "narration" and dm["narration"]:
                note(self.details.realize(d["id"], n, "지문"))
            else:
                picked.append({**d, "mode": mode})
        # 비밀
        for r in dm["reveal"]:
            note(self.character.open_secret(r["id"], n))
        # 캐릭터 특성
        for msg in self.character.update(dm["character_update"], n):
            notes.append({"ok": True, "text": f"특성: {msg}"})
        for g in dm["goal_updates"]:
            note(self.character.set_goal(g["id"], g["status"]))
        # 관계. DM은 제안만 하고, 규칙(±1, 근거, 한 칸씩, 있는 약속만)은 코드가 본다
        if self.relation and (ru := dm.get("relationship_update")):
            self._apply_relation(ru, n, notes)
        # 대화로 넓혀진 세계(유저의 말, 지난 턴 캐릭터의 말). 장면 사실은 항상 보이게 세계에 두고, 쓸모가 있으면 떡밥 풀에도 넣는다
        additions = []
        for add in dm["world_additions"]:
            who, label = add["source"], add["name"] or add["text"]
            if not add["accept"]:
                notes.append({"ok": False, "text": f"세계 확장 안 받음({who}): {label} — {add['why']}"})
                continue
            if add["kind"] == "past":
                self.memory.long_term.append(add["text"])
                result = {"accepted": True, "kind": "past", "text": add["text"]}
                notes.append({"ok": True, "text": f"기억으로 받음({who}): {add['text']}"})
            else:
                result = w.expand(add, n, self.rng)
                extra = f", 주사위 {result['roll']} → " + ("이벤트 추가" if "event" in result else "이벤트 없음")
                notes.append({"ok": True, "text": f"세계 확장({who}, {add['kind']}): {label}{extra}"})
                if result.get("kind") == "location":
                    self._arrive(result["id"], add["arrive"], notes)
            if add["affordances"]:
                result["detail"] = self.details.add_fact(add["text"], add["affordances"], n, source=who)
                notes.append({"ok": True, "text": f"떡밥 풀에도 넣음: {result['detail']}"})
            additions.append({**add, "result": result})
        # 장면 사실. DM은 검색된 사실만 보고, 그중 캐릭터에게 보일 것을 고른다. 이번 턴에 새로 생긴 사실은 DM이 방금 만든 것이라 함께 보인다
        valid_fact = {p["id"] for p in fact_picks}
        for r in dm["retire_facts"]:
            if r["id"] not in valid_fact:
                notes.append({"ok": False, "text": f"사실 정리 거절: {r['id']}는 이번 검색 후보가 아니다"})
            else:
                note(w.retire_fact(r["id"], n))
        shown = [fid for fid in dm["show_facts"] if fid in valid_fact]
        for fid in set(dm["show_facts"]) - valid_fact:
            notes.append({"ok": False, "text": f"사실 보이기 거절: {fid}는 이번 검색 후보가 아니다"})
        shown += [a["result"]["id"] for a in additions if a["result"].get("kind") == "fact"]
        shown_facts = [f for f in w.active_facts() if f["id"] in shown]
        # 유저가 흘린 사소한 사실 → 장기 기억 + 떡밥 풀
        user_facts = []
        for f in dm["user_facts"]:
            did = self.details.add_fact(f["text"], f["affordances"], n, source="유저")
            self.memory.long_term.append(f["text"])
            user_facts.append({"id": did, **f})
            notes.append({"ok": True, "text": f"유저 디테일 저장: {f['text']}"})
        w.history.append({"turn": n, "clock": w.clock, "char_loc": w.char_loc, "user_loc": w.user_loc,
                          "together": w.together, "battery": round(w.battery, 1), "charging": w.charging})
        return {"notes": notes, "fired_now": fired_now, "picked_details": picked, "additions": additions, "shown_facts": shown_facts,
                "user_facts": user_facts, "narration": narration, "channel": dm["channel"], "minutes": spent}

    def _effects(self, event_id: str, n: int, notes: list) -> None:
        e = self.world.event(event_id)
        for ok, msg in apply_effects(e.get("effects"), world=self.world, relation=self.relation, details=self.details,
                                     turn=n, source=f"이벤트 {event_id}"):
            notes.append({"ok": ok, "text": msg})

    def _apply_relation(self, ru: dict, n: int, notes: list) -> None:
        r = self.relation

        def add(ok_msg):
            ok, msg = ok_msg
            if msg:
                notes.append({"ok": ok, "text": msg})
        for x in ru.get("stats") or []:
            add(r.change_stat(x["id"], x["delta"], x["why"], n))
        if ru.get("stage"):
            add(r.set_stage(ru["stage"]["to"], ru["stage"]["why"], n))
        for x in ru.get("address") or []:
            add(r.set_address(x["who"], x["to"], x["why"], n))
        for x in ru.get("promises") or []:
            add(r.set_promise(x["id"], x["status"], x["why"], n))
        for x in ru.get("new_promises") or []:
            add(r.add_promise(x["by"], x["text"], n))

    def _arrive(self, loc: str, who: str, notes: list) -> None:
        """이번 턴에 새로 생긴 장소로 바로 옮긴다. 위치 필드는 이미 있는 장소만 고를 수 있어서, 새 장소로 가는 이동은 여기서 반영한다."""
        w = self.world
        if who == "character" and w.together:
            notes.append({"ok": False, "text": f"{self.name} 이동 거절: 함께 있던 {josa(self.name, '을/를')} 혼자 {josa(w.loc_name(loc), '으로/로')} 보낼 수는 없다"})
            return
        for x in {"both": ["user", "character"], "user": ["user"], "character": ["character"]}.get(who, []):
            ok, msg = w.move(x, loc)
            notes.append({"ok": ok, "text": msg + " (새 장소)"})
        if w.together and w.char_loc != w.user_loc:
            notes.append({"ok": False, "text": "함께 있다고 했지만 위치가 달라 떨어져 있는 것으로 고쳤다"})
            w.together = False

    # ---- 캐릭터 ----
    def _character(self, now: dict, dm: dict, picked: list, fired_now: list, facts: list) -> dict:
        doc = render.character_doc(world=self.world, details=self.details, character=self.character, memory=self.memory,
                                   now=now, dm=dm, picked_details=picked, fired_now=fired_now, facts=facts, relation=self.relation)
        schema = character_schema(list(self.world.locations), [s["id"] for s in self.character.data["secrets"]])
        out = self.llm.json(self.prompt("character"), doc, schema, label="character", effort="low")
        # 가드레일: 다른 언어가 섞이거나 잠긴 비밀을 털어놓았으면(revealed에 locked 비밀) 같은 문서 끝에 다시 쓰라는 지시를 붙여
        # 다시 쓰게 한다(앞부분이 같아 캐시는 그대로 탄다)
        locked = {x["id"]: x for x in self.character.data["secrets"] if x["status"] == "locked"}
        leaks = lambda o: [sid for sid in o.get("revealed", []) if sid in locked]
        guard, tried_lang, tried_leak = [], False, False
        for i in range(LANG_RETRIES):
            bad, leak = foreign(out["say"]), leaks(out)
            if not bad and not leak:
                break
            asks = []
            if bad:
                tried_lang = True
                guard.append({"ok": False, "text": f"가드레일: 대사에 한국어가 아닌 글자({bad[:12]})가 섞여 다시 쓰게 함 ({i + 1}/{LANG_RETRIES})"})
                asks.append("여기에 한국어가 아닌 글자가 섞였다. 같은 뜻과 행동을 한국어로만 다시 쓴다. 외국어 단어도 한국어로 옮긴다.")
            if leak:
                tried_leak = True
                guard.append({"ok": False, "text": f"가드레일: 잠긴 비밀({', '.join(leak)})을 털어놓아 다시 쓰게 함 ({i + 1}/{LANG_RETRIES})"})
                asks.append("여기서 아직 말할 수 없는 비밀을 털어놓았다. 그 내용은 빼고 cover대로 다시 쓴다. cover: "
                            + " / ".join(locked[sid]["cover"] for sid in leak))
            retry = render.tag("rewrite", render.t(f"직전에 쓴 say: {out['say']}\n" + "\n".join(asks)))
            out = self.llm.json(self.prompt("character"), doc + "\n" + retry, schema, label="character-guard", effort="low")
        if bad := foreign(out["say"]):
            out["say"] = strip_foreign(out["say"])
            guard.append({"ok": False, "text": f"가드레일: 다시 써도 남은 글자({bad[:12]})를 지움"})
        elif tried_lang:
            guard.append({"ok": True, "text": "가드레일: 한국어로 다시 씀"})
        if leak := leaks(out):   # 다시 써도 털어놓으면 대사는 쓰되 비밀 상태는 열지 않는다(DM이 열 때까지 잠긴 채)
            out["revealed"] = [sid for sid in out["revealed"] if sid not in locked]
            guard.append({"ok": False, "text": f"가드레일: 다시 써도 잠긴 비밀({', '.join(leak)})을 털어놓음. 대사는 그대로 두고 비밀은 잠근 채 둠"})
        elif tried_leak:
            guard.append({"ok": True, "text": "가드레일: 잠긴 비밀을 털어놓지 않게 다시 씀"})
        out["_guard"] = guard
        out["_doc"] = doc
        return out

    def _apply_character(self, out: dict, n: int, picked: list[dict]) -> list[dict]:
        notes = []
        self.proposal = None
        move = out.get("move")
        if move and move["to"] != self.world.char_loc:
            if move["alone"]:
                ok, msg = self.world.move("character", move["to"])
                if ok:
                    self.world.together = False
                    msg += f" ({josa(self.name, '이/가')} 혼자 자리를 떴다)"
                notes.append({"ok": ok, "text": msg})
            else:
                self.proposal = move["to"]
                notes.append({"ok": True, "text": f"{josa(self.name, '이/가')} {josa(self.world.loc_name(move['to']), '으로/로')} 같이 가자고 했다(다음 턴에 판단)"})
        # 떡밥은 캐릭터가 실제로 쓴 것만 확정한다. 받지 않은 떡밥을 썼다고 한 것은 무시한다
        used = set(out.get("used_details", []))
        for d in picked:
            if d["id"] in used:
                notes.append({"ok": True, "text": self.details.realize(d["id"], n, self.name)[1]})
            else:
                notes.append({"ok": False, "text": f"떡밥 안 쓰임: {d['id']} (상태 그대로, 다음에 다시 고를 수 있다)"})
        for sid in out.get("revealed", []):
            ok, msg = self.character.mark_revealed(sid, n)
            if msg:
                notes.append({"ok": ok, "text": msg})
        return notes

    # ---- 에피소드 정리 ----
    def _episode(self) -> dict:
        """앞쪽 턴을 요약으로 묶고, 요약에서 사라질 원문 디테일(말, 물건과 그 위의 글, 약속, 정한 것)은 떡밥 풀에 "오늘 밤 T#~T#" 출처로 넣는다."""
        out = self.llm.json(self.prompt("episode"), render.episode_doc(self.memory), EPISODE_SCHEMA, label="dm-episode")
        if not out["close"]:
            return {"closed": False, "text": f"묶을 에피소드 없음: {out['why']}", "raw": out}
        ok, msg = self.memory.close_episode(out["until_turn"], out["title"], out["summary"], out["memories"])
        result = {"closed": ok, "text": msg, "raw": out, "notes": []}
        if ok:
            ep = self.memory.episodes[-1]
            raw = "\n".join(x.get(k) or "" for x in ep.pop("raw") for k in ("user", "narration", "reply"))
            ep["details"], notes = self.details.add_episode_details(out.get("details") or [], tuple(ep["turns"]), raw)
            result["notes"] = [{"ok": a, "text": b} for a, b in notes]
        return result

    # ---- 기준선: 캐릭터 프롬프트만 ----
    def _baseline(self, user_input: str) -> str:
        self.baseline_messages.append({"role": "user", "content": user_input})
        text = self.llm.text(self.prompt("baseline"), self.baseline_messages, label="baseline")
        text = re.sub(r"\s*\n+\s*", " ", text)
        self.baseline_messages.append({"role": "assistant", "content": text})
        return text

    # ---- 화면용 상태 ----
    def snapshot(self) -> dict:
        c = self.character.data
        return {
            "n": self.n, "name": self.name, "scenario": {"id": self.scenario, **self.world.scenario},
            "world": self.world.snapshot(), "baseline": self.baseline_on,
            "details": [{k: d.get(k) for k in ("id", "seed", "affordances", "status", "uses", "source", "episode", "kind", "origin")} for d in self.details.items],
            "relationship": self.relation.snapshot() if self.relation else None,
            "character": {"dynamic": c["dynamic"], "goals": c["goals"],
                          "secrets": [{k: s.get(k) for k in ("id", "mode", "cover", "fact", "status", "opened_turn", "revealed_turn")} for s in c["secrets"]]},
            "memory": {"long_term": self.memory.long_term, "episodes": self.memory.episodes,
                       "long_term_start": len(c["long_term_memory"]), "raw_turns": [t["n"] for t in self.memory.turns]},
            "tension": self.tension_log, "log": self.log,
            "calls": usage([c for c in self.llm.calls if c["label"] != "baseline"]),
            "baseline_calls": usage([c for c in self.llm.calls if c["label"] == "baseline"]),
        }
