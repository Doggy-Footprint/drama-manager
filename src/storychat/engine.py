"""한 턴의 파이프라인.

직전 턴의 판단을 적용(체크포인트, 떡밥, reveal, 기억) → 프롬프트 조립 → 캐릭터 → 턴 판단과 검증
→ 시간, 프레임, 관계 기억, 사후 압축 → 이벤트 로그

baseline은 비교용이다. 같은 카드와 지난 기억, 장면 설명으로 캐릭터만 부르고 그 밖의 어떤 것도 주입하지 않는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import world as w
from .director import BASE_TOPICS, Director, Judgement, load_data, load_prompt, scene_problems
from .memory import (ALIVE_RECHECK_TURNS, KEEP_RAW_TURNS, MIN_SEGMENT_TURNS, RAW_BUDGET_TURNS, DetailLedger, EpisodeStore,
                     RelationshipMemory, quoted, squash)
from .schema import FRAME_AXES, Checkpoint, TriggerContext, evaluate, hhmm, parse_hhmm, topics_in, validate_trigger

RECALL_TTL_TURNS = 3
WORLD_FACT_TTL_TURNS = 2
SEED_EVERY_TURNS = 3
PAYOFF_MIN_AGE_TURNS = 5  # 살포한 뒤 이만큼은 지나야 회수가 회수처럼 보인다
NO_REPLY = "(응답 없음)"
OPENING = "(방금 {place}에서 만났다. 네가 먼저 말을 건넨다)"  # 두 모드가 같은 첫 문장을 받는다
REVIEW_CONTEXT_MESSAGES = 8
STAGE_CONTEXT_MESSAGES = 4
COMPRESS_RECENT_MESSAGES = 6
CHARACTER_PROMPT = "character_v4"
MODES = ("structured", "baseline")


class EventLog:
    """모든 판정과 상태 변화를 남긴다. 평가와 데모는 이 로그만 읽는다."""

    def __init__(self, path: Path | None = None) -> None:
        self.events: list[dict] = []
        self.path = path
        self.turn = 0
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def emit(self, kind: str, **data) -> None:
        event = {"kind": kind, "turn": self.turn, **data}
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


@dataclass
class Turn:
    user_text: str | None
    system: str
    messages: list[dict]
    shown: object = None


class Engine:
    def __init__(self, llm, card: str, *, scene: str | dict | None = None, mode: str = "structured", log: EventLog | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"모르는 모드: {mode}")
        self.llm = llm
        self.mode = mode
        self.log = log or EventLog()
        self.director = Director(llm)
        self.card = load_data("cards", f"{card}.yaml")
        self.scene = scene if isinstance(scene, dict) else load_data("scenes", f"{scene or self.card['scene']}.yaml")
        # 장면은 카드와 따로 쓰인다. 문장 속 {name}을 이 카드의 인물 이름으로 바꾼다
        self.scene = json.loads(json.dumps(self.scene, ensure_ascii=False).replace("{name}", self.card["name"]))
        problems = scene_problems(self.scene)
        if problems:
            raise ValueError(f"장면 오류: {problems}")
        character = load_prompt(CHARACTER_PROMPT)
        self.rules, self.format_line = character["rules"].strip(), character["format"]
        seed = load_data(self.card["seed"])
        self.history_text = seed["public_history"].strip()
        self.episodes = EpisodeStore(seed["episodes"])
        self.relationship = RelationshipMemory(seed["relationship"])
        self.ledger = DetailLedger(self.scene)
        self.places = list(self.scene["places"])
        self.checkpoints = [Checkpoint(c["id"], c["trigger"], c["inject"], bool(c.get("deferrable")), c.get("deadline"))
                            for c in self.scene.get("checkpoints", [])]
        self.keep = {item["id"]: list(item.get("keep", [])) for item in [*self.scene.get("checkpoints", []), *self.scene.get("foreshadows", [])]}
        self.state = w.WorldState(place=self.places[0])
        self.pending = Judgement()       # 직전 턴의 판단. 이번 턴에 적용한다
        self.transcript: list[dict] = []
        self.raw_start = 1
        self.boundaries: set[int] = {1}
        self.compress_after = 0
        self.fired: set[str] = set()
        self.frame_start = 1
        self.world_facts: list[dict] = []
        self.world_log: list[dict] = []  # 세계에 일어난 사건 전체. 캐릭터가 아니라 시스템이 기억한다
        self.new_facts: list[str] = []   # 이번 턴에 세계에 새로 일어나 유저도 볼 수 있는 일
        self.user_text: str | None = None
        self.closed = False
        self.recalled: list[dict] = []
        self.revealed: list[dict] = []
        self.spoken: set[str] = set()  # 캐릭터가 실제로 털어놓아 상대도 알게 된 히든
        self.last_seed_turn = 0
        self.topics = sorted(set(BASE_TOPICS) | {t for h in self.card["hidden"] for t in topics_in(h["reveal"])})
        for hidden in self.card["hidden"]:
            problems = validate_trigger(hidden["reveal"], {c.id for c in self.checkpoints})
            if problems:
                raise ValueError(f"카드의 reveal 조건 오류 {hidden['id']}: {problems}")
        self.system = "\n\n".join([
            self.card["public"].strip(),
            "[오늘 이곳]\n" + self.scene["intro"].strip(),
            "[둘의 지난 시간] ('나'는 너, '상대'는 지금 네 앞의 그 사람이다)\n" + self.history_text,
            "[평소의 너]\n" + "\n".join(f"- {h['cover']}" for h in self.card["hidden"]),
            self.rules,
        ])
        self.log.emit("card", name=self.card["name"], label=self.card["label"], public=self.card["public"],
                      cover=[h["cover"] for h in self.card["hidden"]], hidden=self.card["hidden"], system=self.system)
        self.log.emit("scene", mode=mode, intro=self.scene["intro"], places=self.places,
                      checkpoints=self.scene.get("checkpoints", []), foreshadows=self.scene.get("foreshadows", []),
                      details=self.scene.get("details", []))

    # ── 1단계: 직전 판단을 적용 ──────────────────────────────
    def _call_json(self, label: str, fn, default):
        from .llm import RefusalError

        try:
            return fn()
        except RefusalError:
            self.log.emit("refused", label=label)
            return default

    def _mark_boundary(self, reason: str) -> None:
        if self.state.turn not in self.boundaries:
            self.boundaries.add(self.state.turn)
            self.log.emit("boundary", reason=reason)

    def _unspoken(self) -> list[dict]:
        return [h for h in self.revealed if h["id"] not in self.spoken]

    def _context(self) -> TriggerContext:
        s = self.state
        return TriggerContext(s.clock, s.turn - self.frame_start, s.view(), self.pending.signals, self.fired)

    def _stage(self, source: str, core: str) -> str:
        """장면 파일의 핵심을 지금 자리와 대화에 맞는 한 문장으로 고친다.
        keep 낱말이 빠지면 핵심이 바뀐 것으로 보고 한 번 다시 쓰게 한다. 그래도 빠지거나 실패하면 핵심을 그대로 쓴다."""
        s = self.state
        lines = [f"{'상대' if m['role'] == 'user' else self.card['name']}: {m['text']}" for m in self.transcript[-STAGE_CONTEXT_MESSAGES:]]
        if self.user_text:
            lines.append(f"상대: {self.user_text}")
        happened = "\n".join(f"- {e['text']}" for e in self.world_log if not e["id"].startswith("frame:")) or "없음"
        situation = (f"지금: {hhmm(s.clock)}, {s.place}, 날씨 {s.temperature}\n장소 목록: {', '.join(self.places)}\n"
                     f"[이미 일어난 일]\n{happened}\n\n[방금 오간 말]\n" + ("\n".join(lines) or "(아직 없음)"))
        keep = self.keep.get(source, [])
        for attempt in range(2):
            text = self._call_json("stage", lambda: self.director.stage(core, situation), "")
            missing = [k for k in keep if k not in text]
            if text and not missing:
                return text
            self.log.emit("stage_rejected", id=source, text=text, missing=missing, attempt=attempt)
            situation += f"\n\n직전 문장은 핵심이 빠졌다(빠진 말: {', '.join(missing) or '전체'}). 핵심의 뜻을 그대로 살려 다시 쓴다."
        return core

    def _world_happened(self, source: str, core: str) -> str:
        s = self.state
        text = self._stage(source, core)
        self.world_facts.append({"text": text, "turn": s.turn, "until": s.turn + WORLD_FACT_TTL_TURNS - 1})
        self.world_log.append({"turn": s.turn, "clock": hhmm(s.clock), "id": source, "text": text, "core": core})
        self.new_facts.append(text)
        self._mark_boundary(f"world:{source}")
        return text

    def _fire_checkpoints(self) -> None:
        s, ctx, pending = self.state, self._context(), self.pending
        for cp in self.checkpoints:
            if cp.id in self.fired or not evaluate(cp.trigger, ctx):
                continue
            quiet_needed = cp.deferrable and pending.tension and not pending.branch
            if quiet_needed and cp.deadline and s.clock < parse_hhmm(cp.deadline):
                self.log.emit("checkpoint_deferred", id=cp.id, clock=hhmm(s.clock), deadline=cp.deadline)
                continue
            self.fired.add(cp.id)
            text = self._world_happened(cp.id, cp.inject)
            self.log.emit("checkpoint_fired", id=cp.id, source="scene", inject=text, core=cp.inject, clock=hhmm(s.clock))

    def _settle_foreshadows(self) -> None:
        """결과 조건이 맞을 때 살포된 떡밥은 일로 벌어지고, 아직 안 뿌려진 떡밥은 버린다."""
        s, ctx = self.state, self._context()
        for detail in self.ledger.foreshadows():
            if detail.status not in ("seed", "planted") or not evaluate(detail.trigger, ctx):
                continue
            if detail.status == "seed":
                self.ledger.drop(detail.id, s.turn)
                self.log.emit("detail_dropped", id=detail.id, text=detail.text, shown=detail.shown)
            elif s.turn - detail.planted["turn"] >= PAYOFF_MIN_AGE_TURNS:
                self.ledger.pay(detail.id, s.turn)
                text = self._world_happened(detail.id, detail.consequence)
                self.log.emit("payoff_injected", id=detail.id, how="world", fact=text, core=detail.consequence)
                self.log.emit("detail_paid", id=detail.id, how="world", quote=text, gap=s.turn - detail.planted["turn"])

    def _check_reveals(self) -> None:
        ctx = self._context()
        known = {h["id"] for h in self.revealed}
        for hidden in self.card["hidden"]:
            if hidden["id"] not in known and evaluate(hidden["reveal"], ctx):
                self.revealed.append(hidden)
                self.log.emit("hidden_revealed", id=hidden["id"], signals=sorted(self.pending.signals))

    def _expire_recalled(self) -> None:
        self.recalled = [r for r in self.recalled if r["until"] >= self.state.turn]

    def _pick_seed(self):
        turn, ctx = self.state.turn, self._context()
        if self.new_facts or turn - self.last_seed_turn < SEED_EVERY_TURNS:
            return None
        for detail in self.ledger.showable():
            if detail.shown_when is None or evaluate(detail.shown_when, ctx):
                detail.shown += 1
                self.last_seed_turn = turn
                self.log.emit("detail_shown", id=detail.id, text=detail.text)
                return detail
        return None

    def prepare(self, user_text: str | None) -> Turn:
        s = self.state
        s.turn += 1
        self.log.turn = s.turn
        self.new_facts = []
        self.user_text = user_text
        self.log.emit("turn_start", user=user_text, clock=hhmm(s.clock), place=s.place)
        if self.mode == "baseline":
            system, messages = self._assemble_baseline(user_text)
            self.log.emit("prompt_assembled", moment="", prompt_chars=sum(len(m["content"]) for m in messages))
            return Turn(user_text, system, messages)
        for change in w.refresh(s):
            self.log.emit("state_changed", **change)
        self._fire_checkpoints()
        self._settle_foreshadows()
        self._check_reveals()
        self._expire_recalled()
        shown = self._pick_seed()
        system, messages, moment = self._assemble(user_text, shown)
        self.log.emit("prompt_assembled", moment=moment, prompt_chars=sum(len(m["content"]) for m in messages))
        return Turn(user_text, system, messages, shown)

    def _history_messages(self) -> list[dict]:
        messages = [{"role": m["role"], "content": m["text"]} for m in self.transcript]
        if messages and messages[0]["role"] == "assistant":
            messages.insert(0, {"role": "user", "content": OPENING.format(place=self.places[0])})
        return messages

    def _said(self, user_text: str | None) -> str:
        return OPENING.format(place=self.places[0]) if user_text is None and self.state.turn == 1 else f"상대: {user_text or '(말없이 곁에 있다)'}"

    def _assemble_baseline(self, user_text: str | None) -> tuple[str, list[dict]]:
        messages = self._history_messages()
        messages.append({"role": "user", "content": f"{self._said(user_text)}\n\n{self.format_line}"})
        return f"{self.system}\n\n[지금 둘의 사이]\n{self.relationship.state}", messages

    def _assemble(self, user_text: str | None, shown) -> tuple[str, list[dict], str]:
        """잘 변하지 않는 것부터 쌓는다. [이번 순간]은 마지막 메시지에만 두고 대화 기록에 남기지 않는다."""
        s = self.state
        self.world_facts = [f for f in self.world_facts if f["until"] >= s.turn]
        current = {f["text"] for f in self.world_facts}
        system = [self.system, f"[지금 둘의 사이]\n{self.relationship.state}"]
        told = [h for h in self.revealed if h["id"] in self.spoken]
        if told:
            system.append("[이미 상대에게 털어놓은 것]\n" + "\n".join(f"- {h['fact']}" for h in told))
        past = [self.episodes.today_summary] if self.episodes.today_summary else []
        past += [f"- {e['clock']} {e['text']}" for e in self.world_log if e["text"] not in current]
        if past:
            system.append("[오늘 앞서 있었던 일]\n" + "\n".join(past))

        messages = self._history_messages()
        moment = [f"시각 {hhmm(s.clock)}, 장소 {s.place}, 날씨 {s.temperature}"]
        if s.hunger == "배고픔":
            moment.append("몸 상태: 배고픔")
        fresh = [f["text"] for f in self.world_facts if f["turn"] == s.turn]
        recent = [f["text"] for f in self.world_facts if f["turn"] != s.turn]
        if fresh:
            moment.append("방금 세계에 일어난 일:\n" + "\n".join(f"- {t}" for t in fresh))
        if recent:
            moment.append("조금 전 세계에 일어난 일:\n" + "\n".join(f"- {t}" for t in recent))
        if shown:
            moment.append(f"눈에 들어오는 것: {shown.text}")
        if self.recalled:
            moment.append("떠오른 기억:\n" + "\n".join(f"- {r['text']}" for r in self.recalled))
        unspoken = self._unspoken()
        if unspoken:
            moment.append("지금 네 마음속에서 올라오는 것:\n" + "\n".join(
                f"- {h['fact']} (지금까지 숨긴 이유: {h['why_hidden']})" for h in unspoken))
        moment_text = "\n".join(moment)
        messages.append({"role": "user", "content": f"[이번 순간]\n{moment_text}\n\n{self._said(user_text)}\n\n{self.format_line}"})
        return "\n\n".join(system), messages, moment_text

    # ── 2단계: 캐릭터 ────────────────────────────────────────
    def generate(self, turn: Turn) -> str:
        from .llm import RefusalError

        try:
            reply = self.llm.chat(turn.system, turn.messages, label="character")
        except RefusalError:
            self.log.emit("refused", label="character")
            return NO_REPLY
        return re.sub(r"\s*\n+\s*", " ", reply).replace("*", "").strip()

    # ── 3단계: 턴 판단과 검증 ────────────────────────────────
    def review(self, turn: Turn, reply: str) -> dict:
        from .director import NO_REVIEW

        s = self.state
        earlier = "\n".join(f"{'유저' if m['role'] == 'user' else '캐릭터'}: {m['text']}"
                            for m in self.transcript[-REVIEW_CONTEXT_MESSAGES:]) or "없음"
        active = ", ".join(r["id"] for r in self.recalled) or "없음"
        parts = [f"화제 목록: {', '.join(self.topics)}", f"장소 목록: {', '.join(self.places)}",
                 f"과거 에피소드 목록:\n{self.episodes.small_index()}", f"이미 떠오른 에피소드: {active}",
                 f"현재 장소: {s.place}", f"지금 둘의 사이: {self.relationship.state}",
                 f"[앞선 대화]\n{earlier}", f"[이번 턴]\n유저: {turn.user_text or '(말 없음)'}\n캐릭터: {reply}"]
        if turn.shown:
            parts.append(f"[보여 준 디테일] {turn.shown.text}")
        unspoken = self._unspoken()
        if unspoken:
            parts.append("[아직 말하지 않은 비밀]\n" + "\n".join(f"{h['id']}: {h['fact']}" for h in unspoken))
        return self._call_json("review", lambda: self.director.review("\n\n".join(parts), self.places, self.topics), dict(NO_REVIEW))

    def _apply_signals(self, review: dict) -> None:
        """판단을 다음 턴을 위해 쌓아 둔다. 기억은 다음 턴 프롬프트에 들어간다."""
        s = self.state
        topics = [t for t in review.get("topics", []) if t in self.topics]
        # 사이가 바뀐 턴은 그 자체로 국면 전환이다. 모델이 branch를 따로 켜지 않아도 같은 신호로 본다
        changed = review.get("relationship_note") or review.get("relationship_shift")
        branch = review.get("branch") or ("관계 변화" if changed else None)
        self.pending = Judgement(bool(review.get("tension")), branch, topics)
        self.log.emit("signals", tension=self.pending.tension, branch=self.pending.branch, topics=topics,
                      recall_small=review.get("recall_small", []))
        if self.pending.branch:
            self._mark_boundary(f"branch:{self.pending.branch}")
        present, found = {r["id"] for r in self.recalled}, []
        for small_id in review.get("recall_small", [])[:2]:
            small = self.episodes.small(small_id)
            if small and small.id not in present:
                present.add(small.id)
                self.recalled.append({"id": small.id, "text": f"{small.title}: {small.summary}", "until": s.turn + RECALL_TTL_TURNS})
                found.append(small.id)
        if found:
            self.log.emit("memory_recalled", big=None, smalls=found)

    def _settle_details(self, turn: Turn, reply: str, review: dict) -> None:
        s = self.state
        if turn.shown and review.get("shown_said_quote"):
            quote = review["shown_said_quote"]
            if quoted(quote, reply):
                self.ledger.plant(turn.shown.id, quote, s.turn)
                self.log.emit("detail_planted", id=turn.shown.id, quote=quote)
            else:
                self.log.emit("detail_rejected", id=turn.shown.id, reason=f"대사에 없는 인용: {quote!r}")
        candidates = {h["id"] for h in self._unspoken()}
        for item in review.get("hidden_spoken", []):
            if item.get("id") not in candidates:
                continue
            if quoted(item.get("quote", ""), reply):
                self.spoken.add(item["id"])
                self.log.emit("hidden_spoken", id=item["id"], quote=item["quote"])
            else:
                self.log.emit("hidden_rejected", id=item["id"], reason=f"대사에 없는 인용: {item.get('quote')!r}")

    def _update_frame(self, review: dict) -> None:
        s = self.state
        for axis in FRAME_AXES:
            proposed, current = review.get(axis.proposal), getattr(s, axis.name)
            if not proposed or squash(proposed) == squash(current):
                continue
            if axis.name == "place" and proposed not in self.places:
                self.log.emit("move_rejected", proposed=proposed)  # 장소는 닫힌 목록이다
                continue
            self.log.emit("frame_changed", axis=axis.name, old=current, new=proposed)
            self.world_log.append({"turn": s.turn, "clock": hhmm(s.clock), "id": f"frame:{axis.name}",
                                   "text": f"{axis.label}: {current} → {proposed}"})
            setattr(s, axis.name, proposed)
            if axis.name == "place":
                self.frame_start = s.turn + 1
                self.boundaries.add(s.turn + 1)
            else:
                self._mark_boundary(f"relationship:{proposed}")

    def _update_relationship(self, review: dict) -> None:
        """사이가 바뀐 말은 압축을 기다리지 않고 그 턴에 관계 기억으로 간다."""
        note = review.get("relationship_note") or review.get("relationship_shift")
        if not note or squash(note) == squash(self.relationship.state):
            return
        change = self.relationship.update(note, "turn", self.state.turn)
        self.log.emit("relationship_updated", **{k: v for k, v in change.items() if k != "turn"})
        self._mark_boundary("relationship")

    def _compress(self) -> None:
        """지나간 이야기가 된 가장 오래된 구간 하나를 사후 압축하고, 오늘 요약을 다시 쓴다."""
        s = self.state
        over = s.turn - self.raw_start + 1 > RAW_BUDGET_TURNS
        if s.turn < self.compress_after and not over:
            return
        later = sorted(b for b in self.boundaries if b >= self.raw_start + MIN_SEGMENT_TURNS)
        end = later[0] if later else None
        if end is None:
            if not over:
                return
            end = self.raw_start + KEEP_RAW_TURNS
            self.boundaries.add(end)
        if end > s.turn + 1 or (end > s.turn - KEEP_RAW_TURNS + 1 and not over):
            return
        fmt = lambda m: f"{'상대' if m['role'] == 'user' else '나'}: {m['text']}"
        segment = "\n".join(fmt(m) for m in self.transcript if m["turn"] < end)
        after = "\n".join(fmt(m) for m in [m for m in self.transcript if m["turn"] >= end][-COMPRESS_RECENT_MESSAGES:])
        events = "\n".join(f"- {e['clock']} {e['text']}" for e in self.world_log if self.raw_start <= e["turn"] < end)
        alive = self._call_json("alive", lambda: self.director.alive(segment, after), False)
        if alive and not over:
            self.compress_after = s.turn + ALIVE_RECHECK_TURNS
            self.log.emit("compress_postponed", start=self.raw_start, end=end - 1)
            return
        raw = self._call_json("compress", lambda: self.director.compress(
            self.relationship.state, self.episodes.today_summary, segment, events), None)
        if raw is None:
            return
        episode = self.episodes.add_today(raw["title"], raw["summary"], (self.raw_start, end - 1))
        self.transcript = [m for m in self.transcript if m["turn"] >= end]
        self.log.emit("episode_compressed", id=episode.id, title=episode.title, summary=episode.summary,
                      start=self.raw_start, end=end - 1, forced=over and alive)
        self.log.emit("today_summary_updated", **self.episodes.revise_today(raw["today_summary"], s.turn))
        self.raw_start = end
        if self.relationship.changed_since(end):
            # 구간 뒤에 이미 사이가 바뀌었다. 옛 구간만 본 요약이 그 변화를 덮으면 안 된다
            self.log.emit("relationship_kept", proposed=raw.get("relationship"))
        elif raw.get("relationship") and squash(raw["relationship"]) != squash(self.relationship.state):
            change = self.relationship.update(raw["relationship"], episode.id, s.turn)
            self.log.emit("relationship_updated", **{k: v for k, v in change.items() if k != "turn"})

    # ── 4단계: 마감 ──────────────────────────────────────────
    def _turn_end(self) -> None:
        s = self.state
        self.log.emit("turn_end", world=s.snapshot(), relationship=self.relationship.state, ledger=self.ledger.snapshot(),
                      today=self.episodes.today_titles(), today_summary=self.episodes.today_summary, raw_start=self.raw_start)

    def _remember(self, turn: Turn, reply: str) -> None:
        if turn.user_text is not None:
            self.transcript.append({"turn": self.state.turn, "role": "user", "text": turn.user_text})
        self.transcript.append({"turn": self.state.turn, "role": "assistant", "text": reply})

    def finish(self, turn: Turn, reply: str, review: dict) -> None:
        s = self.state
        self.log.emit("character_reply", text=reply)
        self.log.emit("review", **review)
        self._apply_signals(review)
        self._settle_details(turn, reply, review)
        self._remember(turn, reply)
        minutes = w.advance(s, review.get("action_type", "short_talk"))
        self.log.emit("clock_advanced", action=review.get("action_type"), minutes=minutes, clock=hhmm(s.clock))
        if review.get("parted") or s.clock >= w.DAY_END:
            s.ended = True
        self._update_frame(review)
        self._update_relationship(review)
        self._compress()
        self._turn_end()

    def close_storyline(self) -> None:
        """스토리라인이 닫히면 남은 원문을 정리하고 오늘 요약과 관계 기억을 마지막으로 고친다."""
        if self.closed:
            return
        self.closed = True
        if self.mode == "baseline":
            return
        s = self.state
        fmt = lambda m: f"{'상대' if m['role'] == 'user' else '나'}: {m['text']}"
        remaining = "\n".join(fmt(m) for m in self.transcript)
        events = "\n".join(f"- {e['clock']} {e['text']}" for e in self.world_log)
        raw = self._call_json("close", lambda: self.director.close(self.relationship.state, self.episodes.today_summary, remaining, events), None)
        if raw is None:
            return
        if remaining:
            episode = self.episodes.add_today(raw["last_title"], raw["last_summary"], (self.raw_start, s.turn))
            self.log.emit("episode_compressed", id=episode.id, title=episode.title, summary=episode.summary,
                          start=self.raw_start, end=s.turn, forced=False)
            self.transcript, self.raw_start = [], s.turn + 1
        self.episodes.today.oneline = raw["oneline"]
        self.log.emit("today_summary_updated", **self.episodes.revise_today(raw["today_summary"], s.turn))
        if squash(raw["relationship"]) != squash(self.relationship.state):
            change = self.relationship.update(raw["relationship"], "today", s.turn)
            self.log.emit("relationship_updated", **{k: v for k, v in change.items() if k != "turn"})
        self.log.emit("storyline_closed", oneline=raw["oneline"], relationship=self.relationship.state,
                      today_summary=self.episodes.today_summary, episodes=[e.title for e in self.episodes.today.smalls],
                      world_log=self.world_log)

    def step(self, user_text: str | None) -> str:
        turn = self.prepare(user_text)
        reply = self.generate(turn)
        if self.mode == "baseline":
            self.log.emit("character_reply", text=reply)
            self._remember(turn, reply)
            self._turn_end()
            return reply
        self.finish(turn, reply, self.review(turn, reply))
        return reply
