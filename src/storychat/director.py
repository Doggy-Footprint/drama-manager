"""드라마 매니저와 시스템: LLM 판단을 받아 코드가 검증한다. 캐릭터에게 지시를 만들지 않는다."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .schema import Checkpoint, validate_trigger

PROMPTS = Path(__file__).parent / "prompts"
DATA = Path(__file__).parent / "data"
DIRECTOR_PROMPT = "director_v6"
BASE_TOPICS = ["영화", "회사 일", "둘의 장래", "음식", "놀이기구", "지난 추억", "우리 관계"]

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "checkpoints": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"}, "inject": {"type": "string"},
                "bound_to": {"type": "string", "enum": ["place", "relationship"]},
                "deferrable": {"type": "boolean"}, "deadline": {"type": ["string", "null"]},
                "trigger": {"type": "string"},
            },
            "required": ["id", "inject", "bound_to", "deferrable", "deadline", "trigger"],
        }},
        "details": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "affordances": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "affordances"],
        }},
    },
    "required": ["checkpoints", "details"],
}
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "tension": {"type": "boolean"}, "branch": {"type": ["string", "null"]},
        "topics": {"type": "array", "items": {"type": "string"}},
        "recall_big": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tension", "branch", "topics", "recall_big"],
}
DRILL_SCHEMA = {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ids"]}
PAYOFF_SCHEMA = {
    "type": "object",
    "properties": {
        "detail_id": {"type": ["string", "null"]}, "how": {"type": "string", "enum": ["dialogue", "world"]},
        "fact": {"type": "string"},
    },
    "required": ["detail_id", "how", "fact"],
}
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": ["short_talk", "activity", "move", "meal"]},
        "move_to": {"type": ["string", "null"]}, "relationship_shift": {"type": ["string", "null"]},
        "parted": {"type": "boolean"}, "shown_said_quote": {"type": ["string", "null"]},
        "payoff_used_quote": {"type": ["string", "null"]},
        "hidden_spoken": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["id", "quote"],
        }},
        "new_details": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"}, "source": {"type": "string", "enum": ["user", "character"]},
                "quote": {"type": "string"}, "affordances": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text", "source", "quote", "affordances"],
        }},
    },
    "required": ["action_type", "move_to", "relationship_shift", "parted", "shown_said_quote", "payoff_used_quote", "hidden_spoken", "new_details"],
}
CLOSE_SCHEMA = {
    "type": "object",
    "properties": {"last_title": {"type": "string"}, "last_summary": {"type": "string"}, "oneline": {"type": "string"},
                   "relationship": {"type": "string"}},
    "required": ["last_title", "last_summary", "oneline", "relationship"],
}
ALIVE_SCHEMA = {"type": "object", "properties": {"alive": {"type": "boolean"}}, "required": ["alive"]}
COMPRESS_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "relationship": {"type": "string"}},
    "required": ["title", "summary", "relationship"],
}


def load_prompt(name: str) -> dict:
    return yaml.safe_load((PROMPTS / f"{name}.yaml").read_text(encoding="utf-8"))


def load_data(*parts: str) -> dict:
    return yaml.safe_load(DATA.joinpath(*parts).read_text(encoding="utf-8"))


@dataclass
class Judgement:
    tension: bool = False
    branch: str | None = None
    topics: list[str] = field(default_factory=list)
    recall_big: list[str] = field(default_factory=list)

    @property
    def signals(self) -> set[str]:
        signals = {f"topic:{t}" for t in self.topics}
        if self.tension:
            signals.add("tension")
        if self.branch:
            signals.add("branch")
        return signals


class Director:
    def __init__(self, llm) -> None:
        self.llm = llm
        self.prompts = load_prompt(DIRECTOR_PROMPT)

    def make_plan(self, frame_text: str, known_ids: set[str]) -> tuple[list[Checkpoint], list[dict], list[dict]]:
        """프레임 플랜을 만든다. 어휘에 맞지 않는 trigger는 한 번 고쳐 받고, 그래도 틀리면 버린다."""
        rejected: list[dict] = []
        prompt = frame_text
        checkpoints: list[Checkpoint] = []
        details: list[dict] = []
        for attempt in range(2):
            raw = self.llm.json(self.prompts["plan_system"], prompt, PLAN_SCHEMA, label="plan")
            details = raw.get("details", [])
            items = raw.get("checkpoints", [])
            ids = known_ids | {str(c.get("id")) for c in items}
            checkpoints, problems = [], []
            for item in items:
                if isinstance(item.get("trigger"), str):  # 재귀 스키마 대신 JSON 문자열로 받는다
                    try:
                        item["trigger"] = json.loads(item["trigger"])
                    except json.JSONDecodeError:
                        pass
                errors = validate_trigger(item.get("trigger"), ids)
                if errors:
                    problems.append({"id": item.get("id"), "trigger": item.get("trigger"), "errors": errors})
                    continue
                deadline = item.get("deadline") if item.get("deferrable") else None
                if deadline and validate_trigger({"clock_gte": deadline}):
                    deadline = None
                checkpoints.append(Checkpoint(str(item["id"]), item["trigger"], item["inject"], "plan",
                                              item.get("bound_to", "place"), bool(item.get("deferrable")) and bool(deadline), deadline))
            rejected.extend(problems)
            if not problems:
                break
            prompt = f"{frame_text}\n\n직전 답의 trigger가 어휘에 맞지 않았다: {problems}\n어휘만 써서 전체를 다시 답한다."
        return checkpoints, details, rejected

    def judge(self, topics: list[str], index: str, active: list[str], transcript: str) -> Judgement:
        prompt = (f"화제 목록: {', '.join(topics)}\n\n과거 에피소드 목록:\n{index}\n\n"
                  f"이미 떠오른 에피소드: {', '.join(active) or '없음'}\n\n최근 대화:\n{transcript}")
        raw = self.llm.json(self.prompts["judge_system"], prompt, JUDGE_SCHEMA, label="judge")
        return Judgement(bool(raw.get("tension")), raw.get("branch") or None,
                         [t for t in raw.get("topics", []) if t in topics], list(raw.get("recall_big", [])))

    def drill(self, big_title: str, smalls: str, transcript: str) -> list[str]:
        prompt = f"큰 에피소드: {big_title}\n작은 에피소드:\n{smalls}\n\n최근 대화:\n{transcript}"
        return list(self.llm.json(self.prompts["drill_system"], prompt, DRILL_SCHEMA, label="drill").get("ids", []))

    def payoff(self, planted: str, transcript: str, events: str = "") -> dict:
        prompt = f"이미 일어난 사건:\n{events or '없음'}\n\n살포된 디테일:\n{planted}\n\n최근 대화:\n{transcript}"
        return self.llm.json(self.prompts["payoff_system"], prompt, PAYOFF_SCHEMA, label="payoff")

    def close(self, relationship: str, episodes: str, remaining: str, events: str) -> dict:
        prompt = (f"현재 관계 설명: {relationship}\n\n오늘의 작은 에피소드:\n{episodes or '없음'}\n\n"
                  f"세계에 일어난 일:\n{events or '없음'}\n\n아직 정리하지 않은 마지막 대화:\n{remaining or '없음'}")
        return self.llm.json(self.prompts["close_system"], prompt, CLOSE_SCHEMA, label="close")

    def analyze(self, text: str) -> dict:
        return self.llm.json(self.prompts["analysis_system"], text, ANALYSIS_SCHEMA, label="analysis")

    def alive(self, segment: str, recent: str) -> bool:
        prompt = f"[지난 구간]\n{segment}\n\n[최근 대화]\n{recent}"
        return bool(self.llm.json(self.prompts["alive_system"], prompt, ALIVE_SCHEMA, label="alive").get("alive"))

    def compress(self, relationship: str, segment: str, events: str = "") -> dict:
        """뒤의 대화를 주지 않아 요약이 구간 밖으로 넘어갈 수 없다."""
        prompt = (f"현재 관계 설명: {relationship}\n\n이 구간에 세계에 일어난 일:\n{events or '없음'}\n\n"
                  f"정리할 구간:\n{segment}")
        return self.llm.json(self.prompts["compress_system"], prompt, COMPRESS_SCHEMA, label="compress")
