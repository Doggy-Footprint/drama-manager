"""한 턴의 파이프라인.

드라마 매니저 판단 → 체크포인트와 reveal 평가(코드) → 기억 검색 → 떡밥 → 프롬프트 조립 → 캐릭터
→ 턴 분석과 검증 → 시간, 프레임, 사후 압축 → 이벤트 로그
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import world as w
from .director import BASE_TOPICS, Director, load_data, load_prompt
from .memory import (ALIVE_RECHECK_TURNS, KEEP_RAW_TURNS, RAW_BUDGET_TURNS, DetailLedger, EpisodeStore,
                     RelationshipMemory, quoted, squash)
from .schema import FRAME_AXES, Checkpoint, TriggerContext, evaluate, hhmm, parse_hhmm, topics_in, validate_trigger

RECALL_TTL_TURNS = 3
WORLD_FACT_TTL_TURNS = 2
SEED_EVERY_TURNS = 3
PAYOFF_MIN_AGE_TURNS = 2
PAYOFF_COOLDOWN_TURNS = 5
NO_REPLY = "(응답 없음)"
OPENING = "(방금 만났다. 네가 먼저 말을 건넨다)"
ANALYSIS_CONTEXT_MESSAGES = 8


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
    judgement: object
    shown: object = None
    payoff: dict | None = None


class Engine:
    def __init__(self, llm, card: str, *, log: EventLog | None = None) -> None:
        self.llm = llm
        self.log = log or EventLog()
        self.director = Director(llm)
        self.card = load_data("cards", f"{card}.yaml")
        self.rules = load_prompt("character_v1")["rules"].strip()
        seed = load_data("seed.yaml")
        self.history_text = seed["public_history"].strip()
        self.episodes = EpisodeStore(seed["episodes"])
        self.relationship = RelationshipMemory(seed["relationship"])
        self.ledger = DetailLedger()
        self.state = w.WorldState()
        self.transcript: list[dict] = []
        self.raw_start = 1
        self.boundaries: set[int] = {1}
        self.compress_after = 0
        self.fired: set[str] = set()
        self.plan: list[Checkpoint] = []
        self.plan_count = 0
        self.frame_start = 1
        self.world_facts: list[dict] = []
        self.world_log: list[dict] = []  # 세계에 일어난 사건 전체. 캐릭터가 아니라 시스템이 기억한다
        self.new_facts: list[str] = []   # 이번 턴에 세계에 새로 일어나 유저도 볼 수 있는 일
        self.closed = False
        self.recalled: list[dict] = []
        self.revealed: list[dict] = []
        self.spoken: set[str] = set()  # 캐릭터가 실제로 털어놓아 상대도 알게 된 히든
        self.offered: set[str] = set()
        self.last_payoff_turn = -PAYOFF_COOLDOWN_TURNS
        self.big_cooldown: dict[str, int] = {}
        self.last_seed_turn = 0
        self.topics = sorted(set(BASE_TOPICS) | {t for h in self.card["hidden"] for t in topics_in(h["reveal"])})
        for hidden in self.card["hidden"]:
            problems = validate_trigger(hidden["reveal"])
            if problems:
                raise ValueError(f"카드의 reveal 조건 오류 {hidden['id']}: {problems}")
        self.system = "\n\n".join([
            self.card["public"].strip(),
            "[둘의 지난 시간]\n" + self.history_text,
            "[평소의 너]\n" + "\n".join(f"- {h['cover']}" for h in self.card["hidden"]),
            self.rules,
        ])
        self.log.emit("card", name=self.card["name"], label=self.card["label"], public=self.card["public"],
                      cover=[h["cover"] for h in self.card["hidden"]], hidden=self.card["hidden"], system=self.system)
        self._make_plan(["start"])

    # ── 프레임 플랜 ──────────────────────────────────────────
    def _frame_text(self) -> str:
        spoken = "\n".join(f"- {d.text}" for d in self.ledger.planted()) or "없음"
        s = self.state
        axes = "\n".join(f"{a.label}: {getattr(s, a.name)}" for a in FRAME_AXES)
        return (f"프레임\n{axes}\n현재 시각: {hhmm(s.clock)}\n"
                f"인물: {self.card['public'].strip()}\n둘의 지난 시간: {self.history_text}\n"
                f"현재 사이: {self.relationship.state}\n이미 나온 디테일:\n{spoken}")

    def _make_plan(self, changed: list[str]) -> None:
        self.plan_count += 1
        plan_id = f"p{self.plan_count}"
        if {a.name for a in FRAME_AXES if a.resets_plan} & set(changed):
            gone = self.ledger.expire_seeds()
            if gone:
                self.log.emit("details_expired", ids=gone)
            self.plan = []
        known = {c.id for c in w.WORLD_CHECKPOINTS} | self.fired | {c.id for c in self.plan}
        checkpoints, seeds, rejected = self.director.make_plan(self._frame_text(), known)
        self.plan = [c for c in self.plan if c.bound_to not in changed]  # 바뀐 축에 묶인 사건은 더 이상 맞지 않는다
        existing = {c.id for c in self.plan} | self.fired
        fresh = [c for c in checkpoints if c.id not in existing]
        self.plan.extend(fresh)
        added = self.ledger.add_seeds(seeds, plan_id)
        self.frame_start = self.state.turn + 1
        self.log.emit("plan_generated", plan=plan_id, changed=changed, frame=self.state.frame,
                      checkpoints=[c.as_dict() for c in fresh], details=[d.__dict__.copy() for d in added], rejected=rejected)

    # ── 1단계: 판단과 주입 ───────────────────────────────────
    def _recent(self, user_text: str | None, count: int = 8) -> str:
        lines = [f"{'상대' if m['role'] == 'user' else '나'}: {m['text']}" for m in self.transcript[-count:]]
        if user_text is not None:
            lines.append(f"상대: {user_text}")
        return "\n".join(lines) or "(대화 시작 전)"

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

    def _world_happened(self, source: str, text: str) -> None:
        s = self.state
        self.world_facts.append({"text": text, "until": s.turn + WORLD_FACT_TTL_TURNS - 1})
        self.world_log.append({"turn": s.turn, "clock": hhmm(s.clock), "id": source, "text": text})
        self.new_facts.append(text)

    def _fire_checkpoints(self, judgement) -> None:
        s = self.state
        ctx = TriggerContext(s.clock, s.turn - self.frame_start, s.view(), judgement.signals, self.fired)
        for cp in [*w.WORLD_CHECKPOINTS, *self.plan]:
            if cp.id in self.fired or not evaluate(cp.trigger, ctx):
                continue
            quiet_needed = cp.deferrable and judgement.tension and not judgement.branch
            if quiet_needed and cp.deadline and s.clock < parse_hhmm(cp.deadline):
                self.log.emit("checkpoint_deferred", id=cp.id, clock=hhmm(s.clock), deadline=cp.deadline)
                continue
            self.fired.add(cp.id)
            self._world_happened(cp.id, cp.inject)
            self.log.emit("checkpoint_fired", id=cp.id, source=cp.source, inject=cp.inject, clock=hhmm(s.clock))
            self._mark_boundary(f"checkpoint:{cp.id}")

    def _check_reveals(self, judgement) -> None:
        s = self.state
        ctx = TriggerContext(s.clock, s.turn - self.frame_start, s.view(), judgement.signals, self.fired)
        known = {h["id"] for h in self.revealed}
        for hidden in self.card["hidden"]:
            if hidden["id"] not in known and evaluate(hidden["reveal"], ctx):
                self.revealed.append(hidden)
                self.log.emit("hidden_revealed", id=hidden["id"], signals=sorted(judgement.signals))

    def _recall(self, judgement, user_text: str | None) -> None:
        s = self.state
        self.recalled = [r for r in self.recalled if r["until"] >= s.turn]
        present = {r["id"] for r in self.recalled}
        for big_id in judgement.recall_big[:2]:
            big = self.episodes.bigs.get(big_id)
            if not big or not big.smalls or self.big_cooldown.get(big_id, -1) >= s.turn:
                continue
            self.big_cooldown[big_id] = s.turn + RECALL_TTL_TURNS - 1  # 떠올린 동안은 다시 찾지 않는다
            ids = self._call_json("drill", lambda: self.director.drill(big.title, self.episodes.smalls_text(big_id),
                                                                       self._recent(user_text)), [])
            found = []
            for small_id in ids[:2]:
                small = self.episodes.small(small_id)
                if small and small.id not in present:
                    present.add(small.id)
                    self.recalled.append({"id": small.id, "text": f"{small.title}: {small.summary}",
                                          "until": s.turn + RECALL_TTL_TURNS - 1})
                    found.append(small.id)
            self.log.emit("memory_recalled", big=big_id, smalls=found)

    def _pick_payoff(self, judgement, user_text: str | None) -> dict | None:
        if not (judgement.tension or judgement.branch):
            return None
        turn = self.state.turn
        if turn - self.last_payoff_turn < PAYOFF_COOLDOWN_TURNS:
            return None
        ready = [d for d in self.ledger.planted()
                 if d.id not in self.offered and d.planted["turn"] <= turn - PAYOFF_MIN_AGE_TURNS]
        if not ready:
            return None
        listing = "\n".join(f"- {d.id}: {d.text} (쓰임새: {', '.join(d.affordances)})" for d in ready)
        raw = self._call_json("payoff", lambda: self.director.payoff(listing, self._recent(user_text)), {})
        detail_id = raw.get("detail_id")
        if detail_id not in {d.id for d in ready}:
            self.log.emit("payoff_none", candidates=[d.id for d in ready], proposed=detail_id)
            return None
        self.offered.add(detail_id)
        self.last_payoff_turn = turn
        self.log.emit("payoff_injected", id=detail_id, how=raw.get("how"), fact=raw.get("fact"))
        return {"id": detail_id, "how": raw.get("how", "dialogue"), "fact": raw.get("fact", "")}

    def _pick_seed(self, payoff):
        turn = self.state.turn
        if payoff or turn - self.last_seed_turn < SEED_EVERY_TURNS:
            return None
        detail = self.ledger.next_seed()
        if detail:
            detail.shown += 1
            self.last_seed_turn = turn
            self.log.emit("detail_shown", id=detail.id, text=detail.text)
        return detail

    def prepare(self, user_text: str | None) -> Turn:
        from .director import Judgement

        s = self.state
        s.turn += 1
        self.log.turn = s.turn
        self.new_facts = []
        self.log.emit("turn_start", user=user_text, clock=hhmm(s.clock), place=s.place)
        for change in w.refresh(s):
            self.log.emit("state_changed", **change)
        if user_text is None and s.turn == 1:
            judgement = Judgement()
        else:
            judgement = self._call_json("judge", lambda: self.director.judge(
                self.topics, self.episodes.index_text(),
                [b for b, until in self.big_cooldown.items() if until >= s.turn], self._recent(user_text)), Judgement())
        self.log.emit("signals", tension=judgement.tension, branch=judgement.branch, topics=judgement.topics,
                      recall_big=judgement.recall_big)
        if judgement.branch:
            self._mark_boundary(f"branch:{judgement.branch}")
        self._fire_checkpoints(judgement)
        self._check_reveals(judgement)
        self._recall(judgement, user_text)
        payoff = self._pick_payoff(judgement, user_text)
        if payoff and payoff["how"] == "world":
            self._world_happened(payoff["id"], payoff["fact"])
        shown = self._pick_seed(payoff)
        system, messages, moment = self._assemble(user_text, payoff, shown)
        self.log.emit("prompt_assembled", moment=moment, prompt_chars=sum(len(m["content"]) for m in messages))
        return Turn(user_text, system, messages, judgement, shown, payoff)

    def _assemble(self, user_text: str | None, payoff: dict | None, shown) -> tuple[str, list[dict], str]:
        """잘 변하지 않는 것부터 쌓는다. [이번 순간]은 마지막 메시지에만 두고 대화 기록에 남기지 않는다."""
        s = self.state
        self.world_facts = [f for f in self.world_facts if f["until"] >= s.turn]
        current = {f["text"] for f in self.world_facts}
        system = [self.system, f"[지금 둘의 사이]\n{self.relationship.state}"]
        told = [h for h in self.revealed if h["id"] in self.spoken]
        if told:
            system.append("[이미 상대에게 털어놓은 것]\n" + "\n".join(f"- {h['fact']}" for h in told))
        past = [f"- {t}" for t in self.episodes.today_titles()]
        past += [f"- {e['clock']} {e['text']}" for e in self.world_log if e["text"] not in current]
        if past:
            system.append("[오늘 앞서 있었던 일]\n" + "\n".join(past))

        messages = [{"role": m["role"], "content": m["text"]} for m in self.transcript]
        if messages and messages[0]["role"] == "assistant":
            messages.insert(0, {"role": "user", "content": OPENING})

        moment = [f"시각 {hhmm(s.clock)}, 장소 {s.place}, 날씨 {s.temperature}"]
        if s.hunger == "배고픔":
            moment.append("몸 상태: 배고픔")
        if self.world_facts:
            moment.append("방금 세계에 일어난 일:\n" + "\n".join(f"- {f['text']}" for f in self.world_facts))
        if shown:
            moment.append(f"눈에 들어오는 것: {shown.text}")
        memories = [r["text"] for r in self.recalled]
        if payoff and payoff["how"] == "dialogue":
            memories.append(payoff["fact"])
        if memories:
            moment.append("떠오른 기억:\n" + "\n".join(f"- {m}" for m in memories))
        unspoken = self._unspoken()
        if unspoken:
            moment.append("지금 네 마음속에서 올라오는 것:\n" + "\n".join(
                f"- {h['fact']} (지금까지 숨긴 이유: {h['why_hidden']})" for h in unspoken))
        moment_text = "\n".join(moment)
        said = OPENING if user_text is None and s.turn == 1 else f"상대: {user_text or '(말없이 곁에 있다)'}"
        messages.append({"role": "user", "content": f"[이번 순간]\n{moment_text}\n\n{said}"})
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

    # ── 3단계: 분석과 검증 ───────────────────────────────────
    def analyze(self, turn: Turn, reply: str) -> dict:
        earlier = "\n".join(f"{'유저' if m['role'] == 'user' else '캐릭터'}: {m['text']}"
                            for m in self.transcript[-ANALYSIS_CONTEXT_MESSAGES:]) or "없음"
        parts = [f"현재 장소: {self.state.place}", f"[앞선 대화]\n{earlier}",
                 f"[이번 턴]\n유저: {turn.user_text or '(말 없음)'}\n캐릭터: {reply}"]
        if turn.shown:
            parts.append(f"[보여 준 디테일] {turn.shown.text}")
        if turn.payoff:
            parts.append(f"[주입한 떡밥] {self.ledger.items[turn.payoff['id']].text} / {turn.payoff['fact']}")
        unspoken = self._unspoken()
        if unspoken:
            parts.append("[아직 말하지 않은 비밀]\n" + "\n".join(f"{h['id']}: {h['fact']}" for h in unspoken))
        default = {"action_type": "short_talk", "move_to": None, "relationship_shift": None, "parted": False,
                   "shown_said_quote": None, "payoff_used_quote": None, "hidden_spoken": [], "new_details": []}
        return self._call_json("analysis", lambda: self.director.analyze("\n".join(parts)), default)

    def _settle_details(self, turn: Turn, reply: str, analysis: dict) -> None:
        s = self.state
        if turn.shown and analysis.get("shown_said_quote"):
            quote = analysis["shown_said_quote"]
            if quoted(quote, reply):
                self.ledger.plant(turn.shown.id, quote, s.turn)
                self.log.emit("detail_planted", id=turn.shown.id, quote=quote)
            else:
                self.log.emit("detail_rejected", id=turn.shown.id, reason=f"대사에 없는 인용: {quote!r}")
        if turn.payoff:
            quote = analysis.get("payoff_used_quote")
            if quote and quoted(quote, reply):
                self.ledger.pay(turn.payoff["id"], turn.payoff["how"], s.turn)
                self.log.emit("detail_paid", id=turn.payoff["id"], how=turn.payoff["how"], quote=quote)
            else:
                self.log.emit("payoff_unused", id=turn.payoff["id"], quote=quote)
        candidates = {h["id"] for h in self._unspoken()}
        for item in analysis.get("hidden_spoken", []):
            if item.get("id") in candidates and quoted(item.get("quote", ""), reply):
                self.spoken.add(item["id"])
                self.log.emit("hidden_spoken", id=item["id"], quote=item["quote"])
        known = {squash(d.text) for d in self.ledger.items.values()}
        for item in analysis.get("new_details", [])[:1]:
            source_text = turn.user_text if item.get("source") == "user" else reply
            if squash(item.get("text", "")) in known:
                continue
            if not quoted(item.get("quote", ""), source_text or ""):
                self.log.emit("detail_rejected", reason=f"원문에 없는 인용: {item.get('quote')!r}")
                continue
            detail = self.ledger.add_spoken(item["text"], item["source"], item.get("affordances", []), item["quote"], s.turn)
            self.log.emit("detail_added", id=detail.id, text=detail.text, source=detail.source, quote=item["quote"], affordances=detail.affordances)

    def _update_frame(self, analysis: dict, judgement) -> None:
        s = self.state
        changed = []
        for axis in FRAME_AXES:
            proposed, current = analysis.get(axis.proposal), getattr(s, axis.name)
            if not proposed or squash(proposed) == squash(current) or (axis.needs_branch and not judgement.branch):
                continue
            self.log.emit("frame_changed", axis=axis.name, old=current, new=proposed)
            self.world_log.append({"turn": s.turn, "clock": hhmm(s.clock), "id": f"frame:{axis.name}",
                                   "text": f"{axis.label}: {current} → {proposed}"})
            setattr(s, axis.name, proposed)
            changed.append(axis.name)
        if changed and not s.ended:
            self.boundaries.add(s.turn + 1)
            self._make_plan(changed)

    def _compress(self) -> None:
        """지나간 이야기가 된 가장 오래된 구간 하나를 사후 압축한다. 관계 기억도 함께 갱신한다."""
        s = self.state
        over = s.turn - self.raw_start + 1 > RAW_BUDGET_TURNS
        if s.turn < self.compress_after and not over:
            return
        later = sorted(b for b in self.boundaries if b > self.raw_start)
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
        after = "\n".join(fmt(m) for m in self.transcript if m["turn"] >= end)
        events = "\n".join(f"- {e['clock']} {e['text']}" for e in self.world_log if self.raw_start <= e["turn"] < end)
        raw = self._call_json("compress", lambda: self.director.compress(self.relationship.state, segment, after, events), None)
        if raw is None:
            return
        if raw.get("alive") and not over:
            self.compress_after = s.turn + ALIVE_RECHECK_TURNS
            self.log.emit("compress_postponed", start=self.raw_start, end=end - 1)
            return
        episode = self.episodes.add_today(raw["title"], raw["summary"], (self.raw_start, end - 1))
        self.transcript = [m for m in self.transcript if m["turn"] >= end]
        self.log.emit("episode_compressed", id=episode.id, title=episode.title, summary=episode.summary,
                      start=self.raw_start, end=end - 1, forced=over and bool(raw.get("alive")))
        self.raw_start = end
        if raw.get("relationship") and squash(raw["relationship"]) != squash(self.relationship.state):
            self.relationship.update(raw["relationship"], episode.id, s.turn)
            self.log.emit("relationship_updated", state=raw["relationship"], episode=episode.id)

    # ── 4단계: 마감 ──────────────────────────────────────────
    def finish(self, turn: Turn, reply: str, analysis: dict) -> None:
        s = self.state
        self.log.emit("character_reply", text=reply)
        self.log.emit("analysis", **analysis)
        self._settle_details(turn, reply, analysis)
        if turn.user_text is not None:
            self.transcript.append({"turn": s.turn, "role": "user", "text": turn.user_text})
        self.transcript.append({"turn": s.turn, "role": "assistant", "text": reply})
        minutes = w.advance(s, analysis.get("action_type", "short_talk"))
        self.log.emit("clock_advanced", action=analysis.get("action_type"), minutes=minutes, clock=hhmm(s.clock))
        if analysis.get("parted") or s.clock >= w.DAY_END:
            s.ended = True
        self._update_frame(analysis, turn.judgement)
        self._compress()
        self.log.emit("turn_end", world=s.snapshot(), relationship=self.relationship.state,
                      ledger=self.ledger.snapshot(), today=self.episodes.today_titles(), raw_start=self.raw_start)

    def close_storyline(self) -> None:
        """스토리라인이 닫히면 남은 원문을 정리하고 오늘 전체를 큰 에피소드로 묶는다."""
        if self.closed:
            return
        self.closed = True
        s = self.state
        fmt = lambda m: f"{'상대' if m['role'] == 'user' else '나'}: {m['text']}"
        remaining = "\n".join(fmt(m) for m in self.transcript)
        episodes = "\n".join(f"- {e.title}: {e.summary}" for e in self.episodes.today.smalls)
        events = "\n".join(f"- {e['clock']} {e['text']}" for e in self.world_log)
        raw = self._call_json("close", lambda: self.director.close(self.relationship.state, episodes, remaining, events), None)
        if raw is None:
            return
        if remaining:
            episode = self.episodes.add_today(raw["last_title"], raw["last_summary"], (self.raw_start, s.turn))
            self.log.emit("episode_compressed", id=episode.id, title=episode.title, summary=episode.summary,
                          start=self.raw_start, end=s.turn, forced=False)
            self.transcript, self.raw_start = [], s.turn + 1
        self.episodes.today.oneline = raw["oneline"]
        if squash(raw["relationship"]) != squash(self.relationship.state):
            self.relationship.update(raw["relationship"], "today", s.turn)
            self.log.emit("relationship_updated", state=raw["relationship"], episode="today")
        self.log.emit("storyline_closed", oneline=raw["oneline"], relationship=self.relationship.state,
                      episodes=[e.title for e in self.episodes.today.smalls], world_log=self.world_log)

    def step(self, user_text: str | None) -> str:
        turn = self.prepare(user_text)
        reply = self.generate(turn)
        self.finish(turn, reply, self.analyze(turn, reply))
        return reply
