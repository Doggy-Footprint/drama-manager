"""LLM에 보낼 XML 문서를 만든다. 캐릭터에게는 드라마 매니저가 고른 것만, 드라마 매니저에게는 전부 보낸다.

캐릭터 문서는 바뀌지 않는 것(지난 만남, 기억, 관계, 에피소드, 지난 턴)을 앞에, 매 턴 바뀌는 것(details, world_model, characteristic)을 뒤에 둬서
서버 쪽 프롬프트 캐시가 앞부분을 다시 쓰게 한다. 관계 가운데 자주 바뀌는 수치는 뒤쪽(characteristic)에, 드물게 바뀌는 단계·호칭·약속은 앞쪽에 둔다.
"""
from __future__ import annotations

from html import escape

from .state import Character, Details, Memory, Relationship, World, josa, to_min

CROWD = {1: "한산", 2: "붐빔", 3: "걷기 힘들 만큼 붐빔"}


def tag(_tag: str, body: str = "", **attrs) -> str:
    a = "".join(f' {k}="{escape(str(v))}"' for k, v in attrs.items() if v is not None and v != "")
    return f"<{_tag}{a}>{body}</{_tag}>"


def t(s) -> str:
    return escape(str(s), quote=False)


def lines(name: str, items: list[str], child: str) -> str:
    return tag(name, "".join(f"\n  {tag(child, t(i))}" for i in items) + ("\n" if items else ""))


def turn_xml(turn: dict) -> str:
    body = ""
    if turn.get("narration"):
        body += tag("narration", t(turn["narration"]))
    if turn.get("user"):
        body += tag("user", t(turn["user"]), channel=turn.get("channel"))
    if turn.get("reply"):
        body += tag("reply", t(turn["reply"]))
    return tag("turn", body, n=turn["n"], clock=turn["clock"])


def episodes_xml(memory: Memory) -> str:
    eps = memory.session_episodes
    return tag("recent_episodes", "".join(
        f"\n  {tag('episode', t(e['summary']), title=e['title'], clock='~'.join(e['clock']))}" for e in eps)
        + ("\n" if eps else ""))


def past_episodes_xml(memory: Memory) -> str:
    """오늘 밤 전에 둘이 함께 겪은 만남. 세션 내내 바뀌지 않아 문서 맨 앞에 둔다(캐시)."""
    if not memory.prior:
        return ""
    return tag("past_episodes", "".join(
        f"\n  {tag('episode', t(e['summary']), id=e['id'], title=e['title'], when=e['when'])}" for e in memory.prior) + "\n")


def relationship_xml(rel: Relationship | None, details: Details, *, for_dm: bool) -> str:
    """지금 둘의 사이. 캐릭터에게는 단계·호칭·약속·농담만(수치는 characteristic에), DM에게는 수치와 단계 조건까지."""
    if not rel:
        return ""
    name_of = lambda sid: rel.stats[sid]["name"]
    if for_dm:
        stages = " → ".join(s["name"] + ("(지금)" if s["id"] == rel.stage else "")
                            + (f" [needs {', '.join(f'{name_of(k)}≥{v}' for k, v in s['needs'].items())}]" if s.get("needs") else "")
                            for s in rel.stages)
        body = [tag("stages", t(stages), now=rel.stage)]
        body += [tag("stat", str(v["value"]), id=k, name=v["name"]) for k, v in rel.stats.items()]
    else:
        body = [tag("stage", t(rel.stage_name()))]
    body += [tag("address", t(rel.address["character"]), who="너→유저"), tag("address", t(rel.address["user"]), who="유저→너")]
    body += [tag("promise", t(p["text"]), id=p["id"], by=p["by"], status=p["status"], since=p.get("episode")) for p in rel.promises]
    for did in rel.in_jokes:
        if d := details.get(did):
            body.append(tag("in_joke", t(d["seed"]), id=did))
    return tag("relationship", "".join(f"\n  {b}" for b in body) + "\n")


def items_text(world: World, me: str) -> str:
    """누가 무엇을 들고 있는지. me는 이 글을 받는 쪽(character면 '너')."""
    return ", ".join(f"{v['name']}: {'너' if v['holder'] == me else world.holder_name(v['holder'])}" for v in world.items.values())


def flags_text(world: World) -> str:
    return ", ".join(f"{world.flag_name(k)} {v}" for k, v in world.flags.items())


# ---------------- 캐릭터 ----------------

def character_doc(*, world: World, details: Details, character: Character, memory: Memory, now: dict,
                  dm: dict, picked_details: list[dict], fired_now: list[dict], facts: list[dict],
                  relation: Relationship | None = None) -> str:
    past = "\n".join(turn_xml(x) for x in memory.turns)
    current = tag("turn", (tag("narration", t(now["narration"])) if now.get("narration") else "")
                  + tag("user", t(now["user"]), channel=now["channel"]), n=now["n"], clock=now["clock"])
    parts = [
        past_episodes_xml(memory),
        lines("long_term_memory", memory.long_term, "memory"),
        relationship_xml(relation, details, for_dm=False),
        episodes_xml(memory),
        tag("current_turns", f"\n{past}\n{current}\n" if past else f"\n{current}\n"),
        details_for_character(details, picked_details),
        world_for_character(world, dm, fired_now, facts),
        characteristic_for_character(character, dm.get("directive", ""), relation),
    ]
    return "\n".join(x for x in parts if x)


def details_for_character(details: Details, picked: list[dict]) -> str:
    out = []
    for p in picked:
        d = details.get(p["id"])
        if not d:
            continue
        if d.get("episode"):  # 지난 만남의 것은 그때의 말과 물건을 정확히 떠올리도록 원문을 함께 준다
            body = tag("past", t(d["seed"]), episode=d.get("episode_title")) + (tag("use", t(p["how"])) if p.get("how") else "")
        elif p["mode"] == "plant":
            body = tag("seed", t(d["seed"])) + (tag("use", t(p["how"])) if p.get("how") else "")
        else:  # 이미 나온 것은 원문을 다시 주지 않는다. 같은 문장을 되풀이하지 않게
            body = tag("use", t(p.get("how") or d["seed"]))
        out.append(tag("detail", body, id=d["id"], mode=p["mode"]))
    return tag("details", "".join(f"\n  {x}" for x in out) + ("\n" if out else ""))


def world_for_character(world: World, dm: dict, fired_now: list[dict], facts: list[dict]) -> str:
    when = world.clock
    if cd := world.scenario.get("countdown"):
        left = to_min(cd["at"]) - world.minute
        when += f" ({cd['label']}까지 {left}분)" if left > 0 else ""
    here = world.locations[world.char_loc]
    if world.together:
        user = "곁에 있다"
    elif dm.get("channel") == "face":
        user = f"방금 네 곁에서 이 말을 하고 {world.loc_name(world.user_loc)} 쪽으로 떠나는 중이다"
    else:
        user = f"곁에 없다. 유저는 {world.loc_name(world.user_loc)} 쪽에 있는 것으로 안다"
    phone = phone_text(world)
    body = [
        tag("time", t(when)),
        tag("you", t(f"{here['name']}. {here['desc']} 인파: {CROWD[world.crowd(world.char_loc)]}")),
        tag("user", t(user)),
        tag("channel", dm.get("channel", "face")),
        tag("phone", t(phone)),
        tag("weather", t(f"기온 {world.temperature():.0f}℃, {world.scenario.get('season', '')}")),
    ]
    if world.flags:
        body.append(tag("state", t(flags_text(world))))
    if world.items:
        body.append(tag("items", t(items_text(world, "character"))))
    if dm.get("offscreen"):
        body.append(tag("while_apart", t(dm["offscreen"])))
    now_ids = {e["id"] for e in fired_now}
    for e in fired_now:
        body.append(tag("just_now", t(e["how"])))
    for e in world.ongoing():
        if e["id"] not in now_ids:
            body.append(tag("ongoing", t(e["how"]), since=e["clock"]))
    for f in facts:
        body.append(tag("fact", t(f["text"]), where=world.loc_name(f["where"]) if f["where"] else None))
    body.append(tag("places", t(", ".join(f"{k}={v['name']}" for k, v in world.locations.items()))))
    return tag("world_model", "".join(f"\n  {b}" for b in body) + "\n")


def characteristic_for_character(character: Character, directive: str, relation: Relationship | None = None) -> str:
    d = character.data
    core = [tag("trait", t(x)) for x in d["core"]["traits"]]
    secrets = []
    for s in d["secrets"]:
        if s["mode"] == "buried" and s["status"] == "locked":
            core.append(tag("trait", t(s["cover"])))  # 숨기는 게 없는 듯 지낸다. 비밀이라는 표시도 주지 않는다
            continue
        secrets.append(tag("secret", tag("cover", t(s["cover"])) + tag("fact", t(s["fact"])), id=s["id"], status=s["status"]))
    core.append(tag("speech", t(d["core"]["speech"])))
    dyn = d["dynamic"]
    now = tag("mood", t(dyn["mood"])) + tag("attitude", t(dyn["attitude"])) + tag("want", t(dyn["want"]))
    griev = "".join(f"\n    {tag('grievance', t(g['text']), intensity=g['intensity'])}" for g in dyn["grievances"])
    goals = "".join(f"\n    {tag('goal', t(g['text']), status=g['status'])}" for g in d["goals"])
    body = [
        tag("profile", t(d["profile"])),
        tag("core", "".join(f"\n    {c}" for c in core) + "\n  "),
        tag("now", now),
        *([tag("closeness", t(", ".join(f"{v['name']} {v['value']}/5" for v in relation.stats.values())), stage=relation.stage_name())]
          if relation else []),
        tag("grievances", griev + "\n  "),
        tag("goals", goals + "\n  "),
        tag("secrets", "".join(f"\n    {s}" for s in secrets) + "\n  "),
        tag("now_driving", t(directive)),
    ]
    return tag("characteristic", "".join(f"\n  {b}" for b in body) + "\n")


def phone_text(world: World) -> str:
    charging = ", 충전 중" if world.charging else ""
    if world.phone_on:
        return f"켜짐, 배터리 {world.battery:.0f}%{charging}"
    return f"꺼짐({world.phone_off_at}에 배터리가 다 됐다){charging}"


# ---------------- 드라마 매니저와 검색기 ----------------

def situation_xml(world: World, memory: Memory, user_input: str, n: int, tail: int = 4) -> str:
    recent = "\n".join(turn_xml(x) for x in memory.turns[-tail:])
    name = world.char_name
    where = (f"{josa(name, '과/와')} 유저 모두 {world.loc_name(world.char_loc)}" if world.together
             else f"{josa(name, '은/는')} {world.loc_name(world.char_loc)}, 유저는 {world.loc_name(world.user_loc)} (떨어져 있음)")
    return "\n".join([
        tag("situation", t(f"{world.clock}. {where}. {name}의 휴대폰 {phone_text(world)}")),
        tag("recent_turns", f"\n{recent}\n"),
        tag("user_input", t(user_input), turn=n),
    ])


def fact_xml(world: World, f: dict, **extra) -> str:
    return tag("fact", t(f["text"]), id=f["id"], source=f["source"], where=world.loc_name(f["where"]) if f["where"] else "전체",
               since=f"T{f['turn']}", **extra)


def world_candidates_xml(world: World, events: list[dict], facts: list[dict]) -> str:
    ev = "".join(
        f"\n    {tag('event', t(e['text']), id=e['id'], where=','.join(e['where']), window='~'.join(e['window']), tags=','.join(e.get('tags', [])))}"
        for e in events)
    fa = "".join(f"\n    {fact_xml(world, f)}" for f in facts)
    return tag("candidates", f"\n  {tag('events', ev + chr(10) + '  ')}\n  {tag('facts', fa + chr(10) + '  ')}\n")


def last_use_attrs(details: Details, did: str) -> dict:
    """마지막으로 실제 쓰인 턴·방식과 그때의 쓰임. 같은 쓰임을 되풀이하지 않게 보여 준다."""
    u = details.last_realized(did)
    return {"last_used": f"T{u['turn']} {u['mode']}", "last_how": u.get("how")} if u else {}


def episode_attrs(d: dict) -> dict:
    """지난 만남의 떡밥이면 어느 만남인지와 종류(물건, 대사, 약속, 농담 등)."""
    return {"episode": d.get("episode_title"), "kind": d.get("kind")} if d.get("episode") else {}


def details_candidates_xml(details: Details, items: list[dict]) -> str:
    return tag("candidates", "".join(
        f"\n  {tag('detail', t(d['seed']), id=d['id'], status=d['status'], affordances=','.join(d['affordances']), **episode_attrs(d), **last_use_attrs(details, d['id']))}"
        for d in items) + "\n")


def dm_doc(*, world: World, details: Details, character: Character, memory: Memory, user_input: str, n: int,
           ev_picks: list[dict], fact_picks: list[dict], det_picks: dict, tension_log: list[dict], extracted: list[dict] = (),
           proposal: str | None = None, relation: Relationship | None = None) -> str:
    w = world
    locs = "".join(
        f"\n    {tag('place', t(v['desc']), id=k, name=v['name'], crowd_now=w.crowd(k), crowd_in_30m=w.crowd(k, w.minute + 30), near=','.join(f'{a}:{m}분' for a, m in v.get('near', {}).items()))}"
        for k, v in w.locations.items())
    rates = w.data["states"]["battery"]["drain_per_min"]
    lasts = ", ".join(f"{k}면 약 {int(w.battery / r)}분" for k, r in rates.items()) if w.phone_on else "이미 꺼짐"
    name = w.char_name
    phone = (f"켜짐 {w.battery:.0f}% (버티는 시간: {lasts})" if w.phone_on else f"꺼짐 ({w.phone_off_at})") + (", 충전 중" if w.charging else "")
    world_xml = tag("world", "".join([
        f"\n  {tag('clock', w.clock)}",
        f"\n  {tag('appointment', t(w.scenario['appointment']))}" if w.scenario.get("appointment") else "",
        f"\n  {tag('positions', t(f'{name}={w.char_loc}, 유저={w.user_loc}, 함께={w.together}'))}",
        f"\n  {tag('phone', t(phone), owner=name)}",
        f"\n  {tag('temperature', f'{w.temperature():.0f}℃')}",
        f"\n  {tag('flags', t(flags_text(w)))}" if w.flags else "",
        f"\n  {tag('items', t(items_text(w, '')))}" if w.items else "",
        f"\n  {tag('charge', t(charge_text(w)))}" if w.data['states']['battery'].get('charge_from') else "",
        f"\n  {tag('places', locs + chr(10) + '  ')}",
        f"\n  {tag('routes', t(' / '.join(f'{w.loc_name(k)} {m}분' for k, m in sorted(w.routes(w.char_loc).items(), key=lambda x: x[1]))), origin=name)}",
        f"\n  {tag('routes', t(' / '.join(f'{w.loc_name(k)} {m}분' for k, m in sorted(w.routes(w.user_loc).items(), key=lambda x: x[1]))), origin='유저')}" if w.user_loc != w.char_loc else "",
        "\n"]))
    timeline = tag("timeline", "".join(
        f"\n  {tag('event', t(e['text']), id=e['id'], window='~'.join(e['window']), core='yes' if e.get('core') else None, happened=w.fired[e['id']]['clock'] if e['id'] in w.fired else None)}"
        for e in w.data["events"]) + "\n")
    d = character.data
    secrets = "".join(
        f"\n    {tag('secret', tag('cover', t(s['cover'])) + tag('fact', t(s['fact'])), id=s['id'], mode=s['mode'], status=s['status'])}"
        for s in d["secrets"])
    dyn = d["dynamic"]
    char_xml = tag("character", "".join([
        f"\n  {tag('profile', t(d['profile']))}",
        f"\n  {tag('core', ' / '.join(map(t, d['core']['traits'])) + ' / 말투: ' + t(d['core']['speech']))}",
        f"\n  {tag('mood', t(dyn['mood']))}{tag('attitude', t(dyn['attitude']))}{tag('want', t(dyn['want']))}",
        "".join(f"\n  {tag('grievance', t(g['text']), id=g['id'], intensity=g['intensity'])}" for g in dyn["grievances"]),
        "".join(f"\n  {tag('goal', t(g['text']), id=g['id'], status=g['status'])}" for g in d["goals"]),
        f"\n  {tag('secrets', secrets + chr(10) + '  ')}",
        "\n"]))
    turns = "\n".join(turn_xml(x) for x in memory.turns)
    memory_xml = tag("memory", "".join([
        "\n  " + lines("long_term", memory.long_term, "m"),
        "\n  " + episodes_xml(memory),
        f"\n  {tag('turns', chr(10) + turns + chr(10))}",
        "\n"]))
    tension = tag("tension_log", t(" → ".join(f"T{x['n']} {x['beat']} {x['tension']}" for x in tension_log[-10:])))
    planted = [x for x in details.items if x["status"] == "planted"]
    # 이미 나온 떡밥은 참고용이다. 고를 수 있는 것은 <candidates> 안의 것뿐이라 밖에 둔다
    known = tag("planted_details", "".join(f"\n  {tag('detail', t(x['seed']), id=x['id'], **episode_attrs(x), **last_use_attrs(details, x['id']))}" for x in planted) + "\n")
    cand = [
        tag("events", "".join(
            f"\n    {tag('event', t(w.event(p['id'])['text']), id=p['id'], window='~'.join(w.event(p['id'])['window']), where=','.join(w.event(p['id'])['where']), effects=effects_text(w, w.event(p['id'])))}"
            for p in ev_picks) + "\n  "),
        tag("facts", "".join(f"\n    {fact_xml(w, w.fact(p['id']))}" for p in fact_picks) + "\n  "),
        tag("details", "".join(
            f"\n    {tag('detail', t(details.get(p['id'])['seed']), id=p['id'], status=details.get(p['id'])['status'], affordances=','.join(details.get(p['id'])['affordances']), use_as=p['use_as'], **episode_attrs(details.get(p['id'])), **last_use_attrs(details, p['id']))}"
            for p in det_picks.get("picks", [])) + "\n  ", need=det_picks.get("need")),
    ]
    candidates = tag("candidates", "".join(f"\n  {c}" for c in cand) + "\n")
    proposed = tag("proposed_additions", "".join(
        f"\n  {tag('item', t(x['text']), source=x['source'], kind=x['kind'], name=x['name'], near=x['near'])}" for x in extracted)
        + ("\n" if extracted else ""))
    prop = tag("proposal", t(f"{josa(name, '이/가')} {w.loc_name(proposal)}({proposal})로 같이 가자고 했다")) if proposal else ""
    # 잘 안 바뀌는 것(지난 만남, 기억, 캐릭터, 관계)을 앞에, 매 턴 바뀌는 것을 뒤에 둬서 앞부분이 캐시되게 한다
    return "\n".join(x for x in [past_episodes_xml(memory), memory_xml, char_xml, relationship_xml(relation, details, for_dm=True),
                                  timeline, world_xml, tension, known, candidates, proposed, prop,
                                  tag("user_input", t(user_input), turn=n, clock=w.clock)] if x)


def effects_text(w: World, e: dict) -> str | None:
    """이벤트가 일어나면 바뀌는 상태(DM이 결과를 알고 고르게)."""
    eff = e.get("effects") or {}
    parts = [f"{w.flag_name(k)}→{v}" for k, v in (eff.get("set") or {}).items()]
    parts += [f"{w.item_name(k)}→{w.holder_name(v)}" for k, v in (eff.get("give") or {}).items()]
    parts += [f"관계 {k} {v:+d}" for k, v in (eff.get("relation") or {}).items()]
    parts += [f"약속 {k}→{v}" for k, v in (eff.get("promise") or {}).items()]
    parts += [f"떡밥 {d} 드러남" for d in eff.get("plant") or []]
    return "; ".join(parts) or None


def charge_text(w: World) -> str:
    alts = []
    for alt in w.data["states"]["battery"]["charge_from"]:
        bits = []
        if "char_at" in alt:
            bits.append(f"{josa(w.char_name, '이/가')} {'/'.join(map(w.loc_name, alt['char_at']))}에 있다")
        bits += [f"{w.flag_name(k)} {v}" for k, v in (alt.get("flags") or {}).items()]
        bits += [f"{josa(w.holder_name(h), '이/가')} {josa(w.item_name(i), '을/를')} 갖고 있다" for i, h in (alt.get("has") or {}).items()]
        alts.append(" 그리고 ".join(bits))
    return "다음 중 하나일 때만 충전된다: " + " / 또는 ".join(alts)


def episode_doc(memory: Memory) -> str:
    turns = "\n".join(turn_xml(x) for x in memory.turns)
    closable = memory.turns[:-memory.keep_raw]
    return "\n".join([episodes_xml(memory), tag("turns", f"\n{turns}\n"),
                      tag("closable", f"T{closable[0]['n']} ~ T{closable[-1]['n']}" if closable else "없음")])
