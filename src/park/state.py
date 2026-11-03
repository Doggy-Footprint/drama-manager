"""월드 모델, 떡밥, 캐릭터 특성, 기억. 꼭 지켜야 할 규칙은 프롬프트가 아니라 여기서 코드로 막는다.

- 시간은 거꾸로 가지 않는다. 배터리는 줄기만 하고, 0이 되면 휴대폰이 꺼진다.
- 이벤트는 한 번만 일어나고, 정해진 시간 창 안에서만 일어난다. 핵심 이벤트(불꽃놀이)는 시작 5분이 지나면 코드가 일으킨다.
- 캐릭터 특성은 dynamic 쪽만 바뀐다. core는 바뀌지 않는다.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent / "data"
PHONE_USES = ["idle", "photos", "searching", "calling"]
BEATS = ["setup", "complication", "payoff", "breather", "climax", "resolution"]
EXPANSION_EVENT_CHANCE = 0.2
DETAIL_COOLDOWN = 3   # 실제로 쓰인 떡밥은 검색 후보에서 빠진다. 처음 3턴, 쓰일 때마다 두 배(3, 6, 12…)


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def load(name: str):
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8"))


@dataclass
class World:
    data: dict
    minute: int
    prev_minute: int | None = None                 # 이번 턴이 시작된 시각. 시간을 건너뛴 구간의 이벤트를 받기 위해
    char_loc: str = "gate"
    user_loc: str = "gate"
    together: bool = True
    battery: float = 0.0
    phone_on: bool = True
    phone_off_at: str | None = None
    fired: dict = field(default_factory=dict)      # event id -> {turn, clock, how}
    facts: list = field(default_factory=list)      # 대화로 생긴 장면 사실 {id, text, source, turn, where, active}
    history: list = field(default_factory=list)    # 턴마다 {turn, clock, char_loc, user_loc, together, battery}

    @classmethod
    def fresh(cls) -> "World":
        data = load("world.yaml")
        return cls(data=data, minute=to_min(data["start"]), battery=float(data["states"]["battery"]["start"]))

    # --- 읽기 ---
    @property
    def clock(self) -> str:
        return to_clock(self.minute)

    @property
    def locations(self) -> dict:
        return self.data["locations"]

    def loc_name(self, loc: str) -> str:
        return self.locations.get(loc, {}).get("name", loc)

    def crowd(self, loc: str, minute: int | None = None) -> int:
        minute = self.minute if minute is None else minute
        for span in self.data.get("crowd", {}).get(loc, []):
            if to_min(span["from"]) <= minute < to_min(span["to"]):
                return span["level"]
        return 1

    def temperature(self) -> float:
        t = self.data["states"]["temperature"]
        return round(t["start"] + t["per_hour"] * (self.minute - to_min(self.data["start"])) / 60, 1)

    def routes(self, start: str) -> dict[str, int]:
        """start에서 각 장소까지 걸어서 걸리는 최단 시간(분). 붐비는 곳을 지나면 더 걸린다."""
        best, todo = {start: 0}, [start]
        while todo:
            here = min(todo, key=best.get)
            todo.remove(here)
            for there, m in self.locations[here].get("near", {}).items():
                cost = best[here] + m * (2 if self.crowd(there) == 3 else 1)
                if there in self.locations and cost < best.get(there, 10**9):
                    best[there] = cost
                    todo.append(there)
        return best

    def event(self, event_id: str) -> dict | None:
        return next((e for e in self.data["events"] if e["id"] == event_id), None)

    def event_candidates(self, horizon: int = 60) -> list[dict]:
        """아직 일어나지 않았고, 지금부터 horizon분 안에 시간 창이 걸치며, 두 사람 중 한 명이 있는 곳의 이벤트."""
        here = {self.char_loc, self.user_loc}
        out = []
        for e in self.data["events"]:
            if e["id"] in self.fired:
                continue
            start, end = map(to_min, e["window"])
            if end < self.minute or start > self.minute + horizon:
                continue
            if "any" in e["where"] or here & set(e["where"]):
                out.append(e)
        return out

    def ongoing(self) -> list[dict]:
        """이미 일어났고 아직 시간 창 안에 있어 계속 진행 중인 이벤트."""
        return [{"id": eid, **info} for eid, info in self.fired.items()
                if (e := self.event(eid)) and to_min(e["window"][0]) <= self.minute <= to_min(e["window"][1])]

    # --- 쓰기(검증 포함). 각 함수는 (성공, 설명)을 돌려준다 ---
    def advance(self, minutes: int, phone_use: str) -> list[str]:
        minutes = max(0, min(int(minutes), 90))
        self.prev_minute = self.minute
        notes = []
        rate = self.data["states"]["battery"]["drain_per_min"].get(phone_use, 0.15)
        if self.phone_on:
            before = self.battery
            self.battery = max(0.0, self.battery - rate * minutes)
            if self.battery <= 0:
                spent = int(before / rate) if rate else minutes
                self.phone_on = False
                self.phone_off_at = to_clock(self.minute + min(spent, minutes))
                notes.append(f"수아의 휴대폰이 {self.phone_off_at}에 꺼졌다(배터리 0%).")
        self.minute += minutes
        end = to_min(self.data["end"])
        if self.minute > end:
            self.minute = end
        return notes

    def move(self, who: str, loc: str | None) -> tuple[bool, str]:
        if not loc:
            return True, ""
        if loc not in self.locations:
            return False, f"{who} 이동 거절: 모르는 장소 {loc}"
        if who == "수아":
            self.char_loc = loc
        else:
            self.user_loc = loc
        return True, f"{who} → {self.loc_name(loc)}"

    def fire(self, event_id: str, turn: int, how: str) -> tuple[bool, str]:
        e = self.event(event_id)
        if not e:
            return False, f"이벤트 거절: 없는 이벤트 {event_id}"
        if event_id in self.fired:
            return False, f"이벤트 거절: {event_id}는 이미 {self.fired[event_id]['clock']}에 일어났다"
        start, end = map(to_min, e["window"])
        since = self.minute if self.prev_minute is None else self.prev_minute  # 이번 턴에 흐른 구간 안이면 된다
        if end < since or start > self.minute:
            return False, f"이벤트 거절: {event_id}의 시간 창 {e['window'][0]}~{e['window'][1]}이 이번 구간 {to_clock(since)}~{self.clock}과 겹치지 않는다"
        self.fired[event_id] = {"turn": turn, "clock": self.clock, "how": how or e["text"]}
        return True, f"이벤트 발생: {event_id}"

    def forced_core(self, turn: int) -> list[dict]:
        """핵심 이벤트는 시작 5분이 지나도 안 일어났으면 코드가 일으킨다."""
        out = []
        for e in self.data["events"]:
            if e.get("core") and e["id"] not in self.fired and self.minute >= to_min(e["window"][0]) + 5:
                if self.minute <= to_min(e["window"][1]):
                    self.fired[e["id"]] = {"turn": turn, "clock": self.clock, "how": e["text"], "forced": True}
                    out.append(e)
        return out

    def add_fact(self, text: str, source: str, turn: int, where: str | None = None) -> str:
        fid = f"f{len(self.facts) + 1}"
        self.facts.append({"id": fid, "text": text, "source": source, "turn": turn,
                           "where": where if where in self.locations else None, "active": True})
        return fid

    def fact(self, fid: str) -> dict | None:
        return next((x for x in self.facts if x["id"] == fid), None)

    def retire_fact(self, fid: str, turn: int) -> tuple[bool, str]:
        f = self.fact(fid)
        if not f or not f["active"]:
            return False, f"사실 정리 거절: {fid}는 없거나 이미 지난 사실이다"
        f["active"], f["retired_turn"] = False, turn
        return True, f"지난 사실로 정리: {f['text']}"

    def active_facts(self) -> list[dict]:
        """지금도 유효한 장면 사실. 월드 검색기의 후보가 된다."""
        return [f for f in self.facts if f["active"]]

    def expand(self, add: dict, turn: int, rng: random.Random) -> dict:
        """대화로 넓혀진 세계(새 장소, 이 공원에 대한 새 사실)를 받는다. 유저든 수아든 받으면 20% 확률로 그 자리에 이벤트를 하나 더한다."""
        result = {"accepted": False}
        if not add or not add.get("accept") or add.get("kind") not in ("location", "fact"):
            return result
        result["accepted"] = True
        source = add.get("source", "유저")
        if add["kind"] == "location":
            key = f"x{len(self.locations)}"
            anchor = self.user_loc if source == "유저" else self.char_loc
            near = add.get("near") if add.get("near") in self.locations else anchor
            self.locations[key] = {"name": add["name"], "desc": add["text"], "near": {near: 3}, "added_turn": turn, "source": source}
            self.locations[near].setdefault("near", {})[key] = 3
            result.update(kind="location", id=key, name=add["name"])
            where = [key]
        else:
            fid = self.add_fact(add["text"], source, turn, add.get("near"))
            result.update(kind="fact", id=fid, text=add["text"])
            where = [add["near"]] if add.get("near") in self.locations else [self.user_loc, self.char_loc]
        roll = rng.random()
        result["roll"] = round(roll, 2)
        if roll < EXPANSION_EVENT_CHANCE and add.get("event_text"):
            eid = f"x_event_{turn}_{len(self.data['events'])}"
            self.data["events"].append({"id": eid, "where": where, "window": [self.clock, to_clock(self.minute + 40)],
                                        "text": add["event_text"], "tags": [], "added_turn": turn})
            result["event"] = {"id": eid, "text": add["event_text"]}
        return result

    def snapshot(self) -> dict:
        return {"clock": self.clock, "start": self.data["start"], "end": self.data["end"],
                "char_loc": self.char_loc, "user_loc": self.user_loc, "together": self.together,
                "battery": round(self.battery, 1), "phone_on": self.phone_on, "phone_off_at": self.phone_off_at,
                "temperature": self.temperature(),
                "locations": {k: {"name": v["name"], "crowd": self.crowd(k), "added": "added_turn" in v}
                              for k, v in self.locations.items()},
                "events": [{"id": e["id"], "window": e["window"], "text": e["text"], "core": bool(e.get("core")),
                            "added": "added_turn" in e, "fired": self.fired.get(e["id"])} for e in self.data["events"]],
                "facts": self.facts}


@dataclass
class Details:
    items: list

    @classmethod
    def fresh(cls) -> "Details":
        items = load("details.yaml")
        for d in items:
            d.setdefault("where", [])
            d["status"] = "planted" if d.get("planted") else "unplanted"
            d["uses"] = []
            d["source"] = "사전"
        return cls(items=items)

    def get(self, did: str) -> dict | None:
        return next((d for d in self.items if d["id"] == did), None)

    def last_realized(self, did: str) -> dict | None:
        d = self.get(did)
        return next((u for u in reversed(d["uses"]) if u.get("realized")), None) if d else None

    def cooldown(self, did: str, base: int = DETAIL_COOLDOWN) -> int:
        """실제로 쓰인 횟수가 늘수록 길어진다. 같은 떡밥이 몇 턴마다 되풀이되지 않게 한다."""
        d = self.get(did)
        k = sum(1 for u in d["uses"] if u.get("realized")) if d else 0
        return base * 2 ** (k - 1) if k else 0

    def candidates(self, turn: int, base: int = DETAIL_COOLDOWN) -> list[dict]:
        """아직 거두지 않았고, 쿨다운이 지난 떡밥."""
        out = []
        for d in self.items:
            last = self.last_realized(d["id"])
            if d["status"] != "paid_off" and not (last and turn - last["turn"] < self.cooldown(d["id"], base)):
                out.append(d)
        return out

    def attempt(self, did: str, mode: str, how: str, turn: int) -> tuple[bool, str, str]:
        """DM이 고른 떡밥을 검증하고 시도로 기록한다. 상태는 실제로 쓰였을 때(realize) 바뀐다. (성공, 설명, 고친 mode)"""
        d = self.get(did)
        if not d:
            return False, f"떡밥 거절: 없는 떡밥 {did}", mode
        if d["status"] == "paid_off":
            return False, f"떡밥 거절: {did}는 이미 회수됐다", mode
        if mode in ("mention", "payoff") and d["status"] == "unplanted":
            mode = "plant"  # 아직 안 뿌린 떡밥을 회수하려 하면 이번에 뿌리는 것으로 본다
        d["uses"].append({"turn": turn, "mode": mode, "how": how, "realized": False})
        return True, f"떡밥 시도 {mode}: {did}", mode

    def realize(self, did: str, turn: int, by: str) -> tuple[bool, str]:
        """이번 턴에 시도한 떡밥이 실제로 대사나 지문에 나왔다. 이때 상태가 바뀐다."""
        d = self.get(did)
        use = next((u for u in reversed(d["uses"]) if u["turn"] == turn), None) if d else None
        if not use or use["realized"]:
            return False, ""
        use["realized"], use["by"] = True, by
        d["status"] = "paid_off" if use["mode"] == "payoff" else "planted"
        return True, f"떡밥 {use['mode']} 확정({by}): {did} → {d['status']}"

    def add_fact(self, text: str, affordances: list[str], turn: int, source: str = "유저") -> str:
        """대화에서 나온 사실을 이미 뿌린 떡밥으로 넣는다."""
        did = f"{'u' if source == '유저' else 's'}{turn}_{sum(1 for d in self.items if d['source'] == source)}"
        self.items.append({"id": did, "seed": text, "affordances": affordances, "where": [], "status": "planted",
                           "uses": [{"turn": turn, "mode": "plant", "how": f"{source}가 직접 말함", "realized": True, "by": source}],
                           "source": source})
        return did


@dataclass
class Character:
    data: dict

    @classmethod
    def fresh(cls) -> "Character":
        return cls(data=load("character.yaml"))

    @property
    def name(self) -> str:
        return self.data["name"]

    def secret(self, sid: str) -> dict | None:
        return next((s for s in self.data["secrets"] if s["id"] == sid), None)

    def update(self, upd: dict, turn: int) -> list[str]:
        """dynamic 필드, 불만 강도, 새 불만, 목표 상태만 바꾼다. core는 건드리지 않는다."""
        notes = []
        if not upd:
            return notes
        dyn = self.data["dynamic"]
        for key in ("mood", "attitude", "want"):
            if upd.get(key):
                dyn[key] = upd[key]
                notes.append(f"{key} 갱신")
        for g in upd.get("grievances") or []:
            target = next((x for x in dyn["grievances"] if x["id"] == g["id"]), None)
            if target:
                target["intensity"] = max(0, min(3, int(g["intensity"])))
                notes.append(f"불만 {g['id']} → {target['intensity']}")
        if upd.get("new_grievance"):
            dyn["grievances"].append({"id": f"g{turn}", "text": upd["new_grievance"], "intensity": 1, "turn": turn})
            notes.append("새 불만")
        return notes

    def set_goal(self, gid: str, status: str) -> tuple[bool, str]:
        goal = next((g for g in self.data["goals"] if g["id"] == gid), None)
        if not goal:
            return False, f"목표 거절: {gid}"
        goal["status"] = status
        return True, f"목표 {gid} → {status}"

    def open_secret(self, sid: str, turn: int) -> tuple[bool, str]:
        s = self.secret(sid)
        if not s:
            return False, f"비밀 거절: 없는 비밀 {sid}"
        if s["status"] != "locked":
            return False, f"비밀 {sid}는 이미 {s['status']}"
        s["status"], s["opened_turn"] = "revealable", turn
        return True, f"비밀 {sid} → 드러날 수 있음"

    def mark_revealed(self, sid: str, turn: int) -> tuple[bool, str]:
        s = self.secret(sid)
        if not s or s["status"] == "revealed":
            return False, ""
        leaked = s["status"] == "locked"
        s["status"], s["revealed_turn"] = "revealed", turn
        return True, f"비밀 {sid} 털어놓음" + (" (열리기 전에 새어 나감)" if leaked else "")


@dataclass
class Memory:
    long_term: list
    episodes: list = field(default_factory=list)   # {title, summary, turns: [from, to]}
    turns: list = field(default_factory=list)      # 아직 에피소드로 묶이지 않은 턴
    keep_raw: int = 5

    @classmethod
    def fresh(cls, character: Character) -> "Memory":
        return cls(long_term=copy.deepcopy(character.data["long_term_memory"]))

    def close_episode(self, until_turn: int, title: str, summary: str, memories: list[str]) -> tuple[bool, str]:
        closable = [t for t in self.turns[:-self.keep_raw]] if len(self.turns) > self.keep_raw else []
        if not closable or until_turn < closable[0]["n"]:
            return False, "에피소드 보류: 묶을 턴이 없다"
        until_turn = min(until_turn, closable[-1]["n"])  # 최근 5턴은 원문으로 남긴다
        span = [t for t in self.turns if t["n"] <= until_turn]
        self.turns = [t for t in self.turns if t["n"] > until_turn]
        self.episodes.append({"title": title, "summary": summary, "turns": [span[0]["n"], span[-1]["n"]],
                              "clock": [span[0]["clock"], span[-1]["clock"]]})
        self.long_term.extend(memories)
        return True, f"에피소드 '{title}' (T{span[0]['n']}~T{span[-1]['n']})"
