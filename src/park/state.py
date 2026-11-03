"""월드 모델, 떡밥, 캐릭터 특성, 기억. 꼭 지켜야 할 규칙은 프롬프트가 아니라 여기서 코드로 막는다.

- 시간은 거꾸로 가지 않는다. 배터리는 쓰면 줄고 충전 중일 때만 오른다. 0이 되면 휴대폰이 꺼지고, 충전으로 다시 켜진다.
- 이벤트는 한 번만 일어나고, 정해진 시간 창 안에서만 일어난다. 핵심 이벤트는 시작 5분이 지나면 코드가 일으킨다.

이야기(시나리오)마다 scenarios/<id>/ 아래에 world.yaml, character.yaml, details.yaml이 있다.
history.yaml(선택)이 있으면 둘의 지난 만남과 관계 상태로 시작한다.
- 캐릭터 특성은 dynamic 쪽만 바뀐다. core는 바뀌지 않는다.
- 이벤트는 requires(전제 조건)와 effects(상태 변화)를 가질 수 있다. 둘 다 코드가 아는 작은 어휘로만 쓴다(아래 REQUIRES, EFFECTS).
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SCENARIOS = Path(__file__).resolve().parent / "scenarios"
DEFAULT_SCENARIO = "park"
PHONE_USES = ["idle", "photos", "searching", "calling"]
BEATS = ["setup", "complication", "payoff", "breather", "climax", "resolution"]
EXPANSION_EVENT_CHANCE = 0.2
DETAIL_COOLDOWN = 3   # 실제로 쓰인 떡밥은 검색 후보에서 빠진다. 처음 3턴, 쓰일 때마다 두 배(3, 6, 12…)


def josa(word: str, pair: str) -> str:
    """받침에 맞는 조사를 붙인다. pair는 "이/가", "은/는", "을/를", "과/와", "이다/다", "으로/로"처럼 받침 있을 때/없을 때."""
    with_b, without_b = pair.split("/")
    last = word[-1] if word else ""
    batchim = (ord(last) - 0xAC00) % 28 if "가" <= last <= "힣" else 0
    if pair == "으로/로" and batchim == 8:  # ㄹ 받침은 "로"(서울로, 교실로)
        batchim = 0
    return word + (with_b if batchim else without_b)


def fill(template: str, values: dict) -> str:
    """프롬프트의 {{key}}를 채운다. {{name}}이/가처럼 바로 뒤에 조사 쌍이 오면 받침에 맞춰 고른다."""
    def sub(m):
        v = str(values.get(m.group(1), ""))
        return josa(v, m.group(2)) if m.group(2) else v
    return re.sub(r"\{\{(\w+)\}\}(이/가|은/는|을/를|과/와|이다/다|아/야|으로/로)?", sub, template)


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


PHONE_ON_AGAIN = 3.0  # 꺼진 휴대폰은 충전으로 이만큼(%) 차면 다시 켠다


def load(scenario: str, name: str):
    return yaml.safe_load((SCENARIOS / scenario / name).read_text(encoding="utf-8"))


def load_history(scenario: str) -> dict | None:
    """지난 만남과 관계 상태. 없는 이야기는 None(지금까지처럼 동작한다)."""
    return load(scenario, "history.yaml") if (SCENARIOS / scenario / "history.yaml").exists() else None


# ---------------- 이벤트 스키마(온톨로지) ----------------
# 공통 핵심: 시간(window, duration), 장소(where, char_at, user_at), 함께 있음, 소지품(has, give), 관계(relation), 기억(memory, promise, plant).
# 시나리오 확장: world.yaml의 flags. 이름과 값 목록을 시나리오가 정하고, 코드는 그 목록 밖의 값을 받지 않는다.
REQUIRES = {"together", "char_at", "user_at", "flags", "has", "relation", "memory", "after"}
EFFECTS = {"set", "give", "relation", "promise", "plant"}
PEOPLE = ("user", "character")
PROMISE_STATUS = ("open", "kept", "broken")


def requires_ok(req: dict | None, *, world: "World", relation: "Relationship | None" = None,
                details: "Details | None" = None) -> tuple[bool, str]:
    """전제 조건을 지금 상태에 대어 본다. (통과, 못 통과한 이유)"""
    if not req:
        return True, ""
    if "together" in req and world.together != bool(req["together"]):
        return False, "함께 있어야 한다" if req["together"] else "떨어져 있어야 한다"
    if "char_at" in req and world.char_loc not in req["char_at"]:
        return False, f"{josa(world.char_name, '이/가')} {world.loc_name(world.char_loc)}에 있다(필요: {', '.join(map(world.loc_name, req['char_at']))})"
    if "user_at" in req and world.user_loc not in req["user_at"]:
        return False, f"유저가 {world.loc_name(world.user_loc)}에 있다"
    for k, v in (req.get("flags") or {}).items():
        if world.flags.get(k) != v:
            return False, f"{world.flag_name(k)}={world.flags.get(k)} (필요: {v})"
    for item, holder in (req.get("has") or {}).items():
        if not world.holds(item, holder):
            return False, f"{josa(world.item_name(item), '은/는')} {world.holder_name(world.items[item]['holder'])}에게 있다(필요: {world.holder_name(holder)})"
    for eid in req.get("after") or []:
        if eid not in world.fired:
            return False, f"{eid}가 아직 일어나지 않았다"
    if "relation" in req:
        if relation is None:
            return False, "관계 상태가 없는 이야기"
        ok, why = relation.meets(req["relation"])
        if not ok:
            return False, why
    mem = req.get("memory") or {}
    for pid, status in (mem.get("promises") or {}).items():
        p = relation.promise(pid) if relation else None
        if not p or p["status"] != status:
            return False, f"약속 {pid}이 {p['status'] if p else '없음'}(필요: {status})"
    for did in mem.get("recalled") or []:
        if not (details and details.recalled(did)):
            return False, f"떡밥 {did}가 아직 회수되지 않았다"
    for did in mem.get("not_recalled") or []:
        if details and details.recalled(did):
            return False, f"떡밥 {did}는 이미 회수됐다"
    return True, ""


def apply_effects(effects: dict | None, *, world: "World", relation: "Relationship | None", details: "Details | None",
                  turn: int, source: str) -> list[tuple[bool, str]]:
    """이벤트 효과를 상태에 반영한다. 효과는 이야기를 쓴 사람이 정한 것이라 같이 있는지 같은 조건은 보지 않는다."""
    out = []
    if not effects:
        return out
    for k, v in (effects.get("set") or {}).items():
        out.append(world.set_flag(k, v))
    for item, to in (effects.get("give") or {}).items():
        out.append(world.give(item, to, force=True))
    for sid, delta in (effects.get("relation") or {}).items():
        out.append(relation.change_stat(sid, delta, f"{source} 효과", turn, by=source) if relation else (False, "관계 상태가 없다"))
    for pid, status in (effects.get("promise") or {}).items():
        out.append(relation.set_promise(pid, status, f"{source} 효과", turn) if relation else (False, "관계 상태가 없다"))
    for did in effects.get("plant") or []:
        out.append(details.plant(did, turn, source) if details else (False, "떡밥 풀이 없다"))
    return [(ok, f"효과({source}): {msg}") for ok, msg in out if msg]


def validate_schema(world: "World", relation: "Relationship | None", details: "Details") -> list[str]:
    """이벤트와 충전 조건이 코드가 아는 어휘와 이 이야기에 있는 id만 쓰는지 본다. 틀린 곳의 목록(없으면 빈 목록)."""
    errors = []
    stats = set(relation.stats) if relation else set()
    stages = {s["id"] for s in relation.stages} if relation else set()
    promises = {p["id"] for p in relation.promises} if relation else set()
    detail_ids = {d["id"] for d in details.items}
    event_ids = {e["id"] for e in world.data["events"]}
    holders = set(PEOPLE) | {"either"} | set(world.locations)

    def check_req(where: str, req: dict) -> None:
        for k in set(req) - REQUIRES:
            errors.append(f"{where}: 모르는 전제 조건 {k}")
        for k, v in (req.get("flags") or {}).items():
            if k not in world.flag_specs or v not in world.flag_specs[k]["values"]:
                errors.append(f"{where}: 플래그 {k}={v}")
        for item, holder in (req.get("has") or {}).items():
            if item not in world.items or holder not in holders:
                errors.append(f"{where}: 소지품 {item}→{holder}")
        for loc in (req.get("char_at") or []) + (req.get("user_at") or []):
            if loc not in world.locations:
                errors.append(f"{where}: 장소 {loc}")
        for eid in req.get("after") or []:
            if eid not in event_ids:
                errors.append(f"{where}: 이벤트 {eid}")
        rel = req.get("relation") or {}
        if rel and not relation:
            errors.append(f"{where}: 관계 조건인데 history.yaml이 없다")
        for k in set(rel) - {"stage_min", "stage_max", "min", "max"}:
            errors.append(f"{where}: 모르는 관계 조건 {k}")
        for k in ("stage_min", "stage_max"):
            if k in rel and rel[k] not in stages:
                errors.append(f"{where}: 단계 {rel[k]}")
        for sid in list((rel.get("min") or {})) + list((rel.get("max") or {})):
            if sid not in stats:
                errors.append(f"{where}: 관계 수치 {sid}")
        mem = req.get("memory") or {}
        for k in set(mem) - {"promises", "recalled", "not_recalled"}:
            errors.append(f"{where}: 모르는 기억 조건 {k}")
        for pid, st in (mem.get("promises") or {}).items():
            if pid not in promises or st not in PROMISE_STATUS:
                errors.append(f"{where}: 약속 {pid}={st}")
        for did in (mem.get("recalled") or []) + (mem.get("not_recalled") or []):
            if did not in detail_ids:
                errors.append(f"{where}: 떡밥 {did}")

    for e in world.data["events"]:
        where = f"이벤트 {e['id']}"
        check_req(where, e.get("requires") or {})
        eff = e.get("effects") or {}
        for k in set(eff) - EFFECTS:
            errors.append(f"{where}: 모르는 효과 {k}")
        for k, v in (eff.get("set") or {}).items():
            if k not in world.flag_specs or v not in world.flag_specs[k]["values"]:
                errors.append(f"{where}: 플래그 {k}={v}")
        for item, to in (eff.get("give") or {}).items():
            if item not in world.items or to not in set(PEOPLE) | set(world.locations):
                errors.append(f"{where}: 소지품 {item}→{to}")
        for sid in eff.get("relation") or {}:
            if sid not in stats:
                errors.append(f"{where}: 관계 수치 {sid}")
        for pid, st in (eff.get("promise") or {}).items():
            if pid not in promises or st not in PROMISE_STATUS:
                errors.append(f"{where}: 약속 {pid}={st}")
        for did in eff.get("plant") or []:
            if did not in detail_ids:
                errors.append(f"{where}: 떡밥 {did}")
        if "duration" in e and not (isinstance(e["duration"], int) and e["duration"] > 0):
            errors.append(f"{where}: duration은 양의 정수(분)")
    for i, alt in enumerate(world.data["states"]["battery"].get("charge_from") or []):
        check_req(f"충전 조건 {i + 1}", alt)
    return errors


def scenarios() -> list[dict]:
    """고를 수 있는 이야기 목록(화면의 시작 화면용)."""
    out = []
    for d in sorted(SCENARIOS.iterdir()):
        if (d / "world.yaml").exists():
            w, c = load(d.name, "world.yaml"), load(d.name, "character.yaml")
            out.append({"id": d.name, "name": c["name"], **w["scenario"]})
    return sorted(out, key=lambda x: (x.get("order", 99), x["id"]))  # world.yaml의 scenario.order가 작을수록 시작 화면 위에


@dataclass
class World:
    data: dict
    minute: int
    char_name: str = "캐릭터"
    prev_minute: int | None = None                 # 이번 턴이 시작된 시각. 시간을 건너뛴 구간의 이벤트를 받기 위해
    char_loc: str = ""
    user_loc: str = ""
    together: bool = True
    battery: float = 0.0
    phone_on: bool = True
    phone_off_at: str | None = None
    charging: bool = False                         # 보조배터리·충전기에 연결돼 있는지. 드라마 매니저가 정한다
    fired: dict = field(default_factory=dict)      # event id -> {turn, clock, how}
    facts: list = field(default_factory=list)      # 대화로 생긴 장면 사실 {id, text, source, turn, where, active}
    history: list = field(default_factory=list)    # 턴마다 {turn, clock, char_loc, user_loc, together, battery}
    flags: dict = field(default_factory=dict)      # 시나리오 고유 상태(날씨, 전원 등). 이벤트 효과로만 바뀐다
    items: dict = field(default_factory=dict)      # 소지품 id -> {name, desc, holder: user|character|장소 id}

    @classmethod
    def fresh(cls, scenario: str = DEFAULT_SCENARIO, char_name: str = "캐릭터") -> "World":
        data = load(scenario, "world.yaml")
        start = data["start_loc"]
        return cls(data=data, minute=to_min(data["start"]), char_name=char_name, char_loc=start["character"],
                   user_loc=start["user"], battery=float(data["states"]["battery"]["start"]),
                   flags={k: v["value"] for k, v in (data.get("flags") or {}).items()},
                   items=copy.deepcopy(data.get("items") or {}))

    @property
    def scenario(self) -> dict:
        return self.data["scenario"]

    # --- 읽기 ---
    @property
    def clock(self) -> str:
        return to_clock(self.minute)

    @property
    def locations(self) -> dict:
        return self.data["locations"]

    def loc_name(self, loc: str) -> str:
        return self.locations.get(loc, {}).get("name", loc)

    @property
    def flag_specs(self) -> dict:
        return self.data.get("flags") or {}

    def flag_name(self, k: str) -> str:
        return self.flag_specs.get(k, {}).get("name", k)

    def item_name(self, item: str) -> str:
        return self.items.get(item, {}).get("name", item)

    def holder_name(self, holder: str) -> str:
        return {"user": "유저", "character": self.char_name, "either": "두 사람 중 한 명"}.get(holder) or self.loc_name(holder)

    def holds(self, item: str, holder: str) -> bool:
        """holder는 user, character, either(둘 중 한 명), 또는 장소 id."""
        h = self.items.get(item, {}).get("holder")
        return h in PEOPLE if holder == "either" else h == holder

    def where_is(self, holder: str) -> str:
        return {"user": self.user_loc, "character": self.char_loc}.get(holder, holder)

    def set_flag(self, k: str, v) -> tuple[bool, str]:
        spec = self.flag_specs.get(k)
        if not spec:
            return False, f"플래그 거절: 없는 플래그 {k}"
        if v not in spec["values"]:
            return False, f"플래그 거절: {self.flag_name(k)}에 {v}는 없는 값이다({', '.join(map(str, spec['values']))})"
        before, self.flags[k] = self.flags.get(k), v
        return True, f"{self.flag_name(k)} {before} → {v}"

    def give(self, item: str, to: str, *, force: bool = False) -> tuple[bool, str]:
        """물건을 옮긴다. 사람끼리는 함께 있을 때만, 장소에 있는 것은 그 장소에 있는 사람만 집는다. force는 이벤트 효과."""
        it = self.items.get(item)
        if not it:
            return False, f"소지품 거절: 없는 물건 {item}"
        if to not in PEOPLE and to not in self.locations:
            return False, f"소지품 거절: {to}는 사람도 장소도 아니다"
        frm = it["holder"]
        if frm == to:
            return False, ""
        if not force:
            a, b = self.where_is(frm), self.where_is(to)
            if frm in PEOPLE and to in PEOPLE and not self.together:
                return False, f"소지품 거절: {josa(self.holder_name(frm), '과/와')} {josa(self.holder_name(to), '이/가')} 떨어져 있어 {josa(self.item_name(item), '을/를')} 건넬 수 없다"
            if a != b:
                return False, f"소지품 거절: {josa(self.item_name(item), '은/는')} {self.loc_name(a)}에 있고 받는 쪽은 {self.loc_name(b)}에 있다"
        it["holder"] = to
        return True, f"{self.item_name(item)}: {self.holder_name(frm)} → {self.holder_name(to)}"

    def can_charge(self, relation=None, details=None) -> tuple[bool, str]:
        """충전할 수 있는지. world.yaml의 battery.charge_from(전제 조건 목록) 중 하나라도 맞으면 된다. 없으면 늘 된다(예전 이야기)."""
        alts = self.data["states"]["battery"].get("charge_from")
        if not alts:
            return True, ""
        whys = []
        for alt in alts:
            ok, why = requires_ok(alt, world=self, relation=relation, details=details)
            if ok:
                return True, ""
            whys.append(why)
        return False, " / ".join(whys)

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

    def event_candidates(self, horizon: int = 60, gate=None, blocked: list | None = None) -> list[dict]:
        """아직 일어나지 않았고, 지금부터 horizon분 안에 시간 창이 걸치며, 두 사람 중 한 명이 있는 곳의 이벤트.
        gate(e) -> (통과, 이유)가 있으면 requires도 본다. 못 통과한 것은 blocked에 이유와 함께 남기고 검색기에 보내지 않는다."""
        here = {self.char_loc, self.user_loc}
        out = []
        for e in self.data["events"]:
            if e["id"] in self.fired:
                continue
            start, end = map(to_min, e["window"])
            if end < self.minute or start > self.minute + horizon:
                continue
            if "any" in e["where"] or here & set(e["where"]):
                ok, why = gate(e) if gate else (True, "")
                if ok:
                    out.append(e)
                elif blocked is not None:
                    blocked.append({"id": e["id"], "why": why})
        return out

    def ongoing(self) -> list[dict]:
        """이미 일어났고 아직 진행 중인 이벤트. duration(분)이 있으면 일어난 때부터 그만큼, 없으면 시간 창 끝까지."""
        out = []
        for eid, info in self.fired.items():
            e = self.event(eid)
            if not e:
                continue
            end = to_min(info["clock"]) + e["duration"] if "duration" in e else to_min(e["window"][1])
            if to_min(e["window"][0]) <= self.minute <= end:
                out.append({"id": eid, **info})
        return out

    # --- 쓰기(검증 포함). 각 함수는 (성공, 설명)을 돌려준다 ---
    def advance(self, minutes: int, phone_use: str) -> list[str]:
        minutes = max(0, min(int(minutes), 90))
        self.prev_minute = self.minute
        notes = []
        bat = self.data["states"]["battery"]
        drain = bat["drain_per_min"].get(phone_use, 0.15) if self.phone_on else 0.0
        net = (bat.get("charge_per_min", 0.0) if self.charging else 0.0) - drain   # 분당 변화. 충전 중이면 쓰면서도 오를 수 있다
        before = self.battery
        self.battery = max(0.0, min(100.0, self.battery + net * minutes))
        if self.phone_on and self.battery <= 0:
            spent = int(before / -net) if net < 0 else minutes
            self.phone_on = False
            self.phone_off_at = to_clock(self.minute + min(spent, minutes))
            notes.append(f"{self.char_name}의 휴대폰이 {self.phone_off_at}에 꺼졌다(배터리 0%).")
        elif not self.phone_on and self.battery >= PHONE_ON_AGAIN:
            self.phone_on, self.phone_off_at = True, None
            notes.append(f"충전으로 {self.char_name}의 휴대폰이 다시 켜졌다({self.battery:.0f}%).")
        if self.charging:
            notes.append(f"충전 중: 배터리 {before:.0f}% → {self.battery:.0f}%")
        self.minute += minutes
        end = to_min(self.data["end"])
        if self.minute > end:
            self.minute = end
        return notes

    def move(self, who: str, loc: str | None) -> tuple[bool, str]:
        """who는 "character" 또는 "user"."""
        label = self.char_name if who == "character" else "유저"
        if not loc:
            return True, ""
        if loc not in self.locations:
            return False, f"{label} 이동 거절: 모르는 장소 {loc}"
        if who == "character":
            self.char_loc = loc
        else:
            self.user_loc = loc
        return True, f"{label} → {self.loc_name(loc)}"

    def fire(self, event_id: str, turn: int, how: str, gate=None) -> tuple[bool, str]:
        e = self.event(event_id)
        if not e:
            return False, f"이벤트 거절: 없는 이벤트 {event_id}"
        if event_id in self.fired:
            return False, f"이벤트 거절: {event_id}는 이미 {self.fired[event_id]['clock']}에 일어났다"
        start, end = map(to_min, e["window"])
        since = self.minute if self.prev_minute is None else self.prev_minute  # 이번 턴에 흐른 구간 안이면 된다
        if end < since or start > self.minute:
            return False, f"이벤트 거절: {event_id}의 시간 창 {e['window'][0]}~{e['window'][1]}이 이번 구간 {to_clock(since)}~{self.clock}과 겹치지 않는다"
        ok, why = gate(e) if gate else (True, "")
        if not ok:  # 검색 뒤 이번 턴에 상태가 바뀌었을 수 있어 일어나는 순간에 다시 본다
            return False, f"이벤트 거절: {event_id}의 전제 조건이 맞지 않는다({why})"
        self.fired[event_id] = {"turn": turn, "clock": self.clock, "how": how or e["text"]}
        return True, f"이벤트 발생: {event_id}"

    def forced_core(self, turn: int, gate=None) -> list[dict]:
        """핵심 이벤트는 시작 5분이 지나도 안 일어났으면 코드가 일으킨다.
        한 턴에 시간이 창을 통째로 건너뛰어도(21:58 → 22:25) 놓치지 않게, 이번 턴에 흐른 구간이 창과 겹치면 일으킨다.
        requires가 있으면 맞을 때만 일으킨다."""
        out = []
        since = self.minute if self.prev_minute is None else self.prev_minute
        for e in self.data["events"]:
            if e.get("core") and e["id"] not in self.fired and self.minute >= to_min(e["window"][0]) + 5:
                if since <= to_min(e["window"][1]) and (not gate or gate(e)[0]):
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
        """대화로 넓혀진 세계(새 장소, 이 무대에 대한 새 사실)를 받는다. 유저든 캐릭터든 받으면 20% 확률로 그 자리에 이벤트를 하나 더한다."""
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
                "battery": round(self.battery, 1), "phone_on": self.phone_on, "phone_off_at": self.phone_off_at, "charging": self.charging,
                "temperature": self.temperature(),
                "locations": {k: {"name": v["name"], "crowd": self.crowd(k), "added": "added_turn" in v, "xy": v.get("xy"),
                                  "near": list(v.get("near", {}))}
                              for k, v in self.locations.items()},
                "events": [{"id": e["id"], "window": e["window"], "text": e["text"], "core": bool(e.get("core")),
                            "added": "added_turn" in e, "fired": self.fired.get(e["id"]),
                            **{k: e[k] for k in ("requires", "effects", "duration") if k in e}} for e in self.data["events"]],
                "flags": [{"id": k, "name": self.flag_name(k), "value": v} for k, v in self.flags.items()],
                "items": [{"id": k, "name": v["name"], "holder": v["holder"], "holder_name": self.holder_name(v["holder"])}
                          for k, v in self.items.items()],
                "facts": self.facts}


@dataclass
class Details:
    items: list

    @classmethod
    def fresh(cls, scenario: str = DEFAULT_SCENARIO, history: dict | None = None) -> "Details":
        items = load(scenario, "details.yaml")
        for d in items:
            d.setdefault("where", [])
            d["status"] = "planted" if d.get("planted") else "unplanted"
            d["uses"] = []
            d["source"] = "사전"
        # 지난 만남의 디테일은 둘 다 아는 것이라 처음부터 뿌려진 떡밥이다. 회수(mention/payoff)되면 "기억하고 있다"가 보인다
        for ep in (history or {}).get("episodes", []):
            for d in ep.get("details", []):
                items.append({"id": d["id"], "seed": d["text"], "affordances": d["affordances"], "where": [],
                              "status": "planted", "uses": [], "source": "지난 만남", "episode": ep["id"],
                              "episode_title": ep["title"], "kind": d.get("kind", "")})
        return cls(items=items)

    def recalled(self, did: str) -> bool:
        """이 세션에서 실제로 대사나 지문에 나온 적이 있는지(지난 만남의 디테일이면 '회수됐다')."""
        d = self.get(did)
        return bool(d) and any(u.get("realized") for u in d["uses"])

    def plant(self, did: str, turn: int, by: str) -> tuple[bool, str]:
        """이벤트 효과로 떡밥을 뿌린다(이벤트 지문에 이미 담겼다고 본다)."""
        d = self.get(did)
        if not d:
            return False, f"떡밥 거절: 없는 떡밥 {did}"
        if d["status"] != "unplanted":
            return False, f"떡밥 {did}는 이미 {d['status']}"
        d["status"] = "planted"
        d["uses"].append({"turn": turn, "mode": "plant", "how": f"{by}로 드러남", "realized": True, "by": by})
        return True, f"떡밥 plant: {did}"

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
    def fresh(cls, scenario: str = DEFAULT_SCENARIO) -> "Character":
        return cls(data=load(scenario, "character.yaml"))

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
class Relationship:
    """둘의 사이. 단계, 수치(0~5), 호칭, 약속. DM이 갱신을 제안하고 코드가 규칙으로 검증한다.
    - 수치는 한 턴에 하나당 ±1, 근거 문장이 있어야 한다. 이벤트 효과로 이미 바뀐 수치는 그 턴에 DM이 다시 못 바꾼다.
    - 단계는 한 번에 한 칸. 올라가려면 그 단계의 needs(수치 하한)를 채워야 한다.
    - 약속은 있는 id만, open에서 kept/broken으로만 바뀐다."""
    stages: list                                   # [{id, name, needs: {stat: n}}] 낮은 단계부터
    stage: str
    stats: dict                                    # id -> {name, value}
    address: dict                                  # {character: 캐릭터가 유저를 부르는 말, user: 유저가 캐릭터를 부르는 말}
    promises: list                                 # {id, by, text, episode, status}
    in_jokes: list                                 # 둘만 아는 농담(떡밥 id)
    log: list = field(default_factory=list)        # {turn, what, text, why, by}
    touched: dict = field(default_factory=dict)    # 이번 턴에 바뀐 것 {key: turn}

    @classmethod
    def fresh(cls, history: dict) -> "Relationship":
        r = copy.deepcopy(history["relationship"])
        return cls(stages=r["stages"], stage=r["stage"], stats=r["stats"], address=r["address"],
                   promises=r.get("promises", []), in_jokes=r.get("in_jokes", []))

    def stage_index(self, sid: str) -> int:
        return next((i for i, s in enumerate(self.stages) if s["id"] == sid), -1)

    def stage_name(self, sid: str | None = None) -> str:
        sid = sid or self.stage
        return next((s["name"] for s in self.stages if s["id"] == sid), sid)

    def value(self, sid: str) -> int:
        return self.stats[sid]["value"]

    def promise(self, pid: str) -> dict | None:
        return next((p for p in self.promises if p["id"] == pid), None)

    def meets(self, req: dict) -> tuple[bool, str]:
        here = self.stage_index(self.stage)
        if "stage_min" in req and here < self.stage_index(req["stage_min"]):
            return False, f"단계 {self.stage_name()} (필요: {self.stage_name(req['stage_min'])} 이상)"
        if "stage_max" in req and here > self.stage_index(req["stage_max"]):
            return False, f"단계 {self.stage_name()} (필요: {self.stage_name(req['stage_max'])} 이하)"
        for sid, n in (req.get("min") or {}).items():
            if self.value(sid) < n:
                return False, f"{self.stats[sid]['name']} {self.value(sid)} < {n}"
        for sid, n in (req.get("max") or {}).items():
            if self.value(sid) > n:
                return False, f"{self.stats[sid]['name']} {self.value(sid)} > {n}"
        return True, ""

    def _record(self, turn: int, what: str, text: str, why: str, by: str) -> None:
        self.log.append({"turn": turn, "what": what, "text": text, "why": why, "by": by})

    def change_stat(self, sid: str, delta: int, why: str, turn: int, by: str = "DM") -> tuple[bool, str]:
        if sid not in self.stats:
            return False, f"관계 거절: 없는 수치 {sid}"
        name = self.stats[sid]["name"]
        if not delta:
            return False, ""
        if by == "DM" and abs(delta) != 1:
            return False, f"관계 거절: {name} {delta:+d} (한 턴에 ±1까지)"
        if not (why or "").strip():
            return False, f"관계 거절: {name} {delta:+d}에 근거가 없다"
        if self.touched.get(sid) == turn:
            return False, f"관계 거절: {josa(name, '은/는')} 이번 턴에 이미 바뀌었다"
        before = self.value(sid)
        after = max(0, min(5, before + delta))
        if after == before:
            return False, f"관계 거절: {josa(name, '이/가')} 이미 {before}이다(0~5)"
        self.stats[sid]["value"] = after
        self.touched[sid] = turn
        self._record(turn, "stat", f"{name} {before}→{after}", why, by)
        return True, f"관계: {name} {before} → {after} ({why})"

    def set_stage(self, to: str, why: str, turn: int) -> tuple[bool, str]:
        i, j = self.stage_index(self.stage), self.stage_index(to)
        if j < 0:
            return False, f"관계 거절: 없는 단계 {to}"
        if i == j:
            return False, ""
        if abs(j - i) > 1:
            return False, f"관계 거절: {self.stage_name()} → {self.stage_name(to)} (단계는 한 칸씩)"
        if not (why or "").strip():
            return False, f"관계 거절: 단계 변경에 근거가 없다"
        if self.touched.get("stage") == turn:
            return False, "관계 거절: 단계는 이번 턴에 이미 바뀌었다"
        if j > i:
            lack = [f"{self.stats[k]['name']} {self.value(k)}<{n}" for k, n in (self.stages[j].get("needs") or {}).items() if self.value(k) < n]
            if lack:
                return False, f"관계 거절: {josa(self.stage_name(to), '으로/로')} 가려면 {', '.join(lack)}"
        before = self.stage_name()
        self.stage = to
        self.touched["stage"] = turn
        self._record(turn, "stage", f"{before}→{self.stage_name()}", why, "DM")
        return True, f"관계 단계: {before} → {self.stage_name()} ({why})"

    def set_address(self, who: str, to: str, why: str, turn: int) -> tuple[bool, str]:
        if who not in self.address:
            return False, f"호칭 거절: {who}"
        to = (to or "").strip()
        if not to or to == self.address[who]:
            return False, ""
        if not (why or "").strip():
            return False, "호칭 거절: 근거가 없다"
        before, self.address[who] = self.address[who], to
        self._record(turn, "address", f"{who}: {before}→{to}", why, "DM")
        return True, f"호칭({'캐릭터→유저' if who == 'character' else '유저→캐릭터'}): {before} → {to}"

    def set_promise(self, pid: str, status: str, why: str, turn: int) -> tuple[bool, str]:
        p = self.promise(pid)
        if not p:
            return False, f"약속 거절: 없는 약속 {pid}"
        if status not in ("kept", "broken"):
            return False, f"약속 거절: {status}로는 바꿀 수 없다(kept, broken만)"
        if p["status"] != "open":
            return False, f"약속 거절: {pid}는 이미 {p['status']}"
        if not (why or "").strip():
            return False, f"약속 거절: {pid} 변경에 근거가 없다"
        p["status"], p["turn"] = status, turn
        self._record(turn, "promise", f"{pid} → {status}", why, "DM")
        return True, f"약속 {pid} → {status} ({why})"

    def add_promise(self, by: str, text: str, turn: int) -> tuple[bool, str]:
        if not (text or "").strip():
            return False, ""
        pid = f"p{turn}_{len(self.promises)}"
        self.promises.append({"id": pid, "by": by, "text": text, "episode": "오늘", "status": "open", "turn": turn})
        self._record(turn, "promise", f"새 약속 {pid}", text, by)
        return True, f"새 약속 {pid}({by}): {text}"

    def snapshot(self) -> dict:
        return {"stages": self.stages, "stage": self.stage, "stats": self.stats, "address": self.address,
                "promises": self.promises, "in_jokes": self.in_jokes, "log": self.log}


@dataclass
class Memory:
    long_term: list
    episodes: list = field(default_factory=list)   # {title, summary, turns: [from, to]}. 지난 만남은 {id, title, when, summary, prior: True}
    turns: list = field(default_factory=list)      # 아직 에피소드로 묶이지 않은 턴
    keep_raw: int = 5

    @classmethod
    def fresh(cls, character: Character, history: dict | None = None) -> "Memory":
        prior = [{"id": ep["id"], "title": ep["title"], "when": ep["when"], "summary": ep["summary"].strip(), "prior": True,
                  "details": [d["id"] for d in ep.get("details", [])]} for ep in (history or {}).get("episodes", [])]
        return cls(long_term=copy.deepcopy(character.data["long_term_memory"]), episodes=prior)

    @property
    def prior(self) -> list:
        return [e for e in self.episodes if e.get("prior")]

    @property
    def session_episodes(self) -> list:
        return [e for e in self.episodes if not e.get("prior")]

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
