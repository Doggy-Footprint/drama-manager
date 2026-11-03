"""드라마 매니저와 시스템: LLM 판단을 받아 코드가 검증한다. 캐릭터에게 지시를 만들지 않는다."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .schema import validate_trigger

PROMPTS = Path(__file__).parent / "prompts"
DATA = Path(__file__).parent / "data"
DIRECTOR_PROMPT = "director_v7"
BASE_TOPICS = ["영화", "회사 일", "둘의 장래", "음식", "놀이기구", "지난 추억", "우리 관계"]

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "places": {"type": "array", "items": {"type": "string"}},
        "checkpoints": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "inject": {"type": "string"}, "deferrable": {"type": "boolean"},
                           "deadline": {"type": ["string", "null"]}, "trigger": {"type": "string"}},
            "required": ["id", "inject", "deferrable", "deadline", "trigger"],
        }},
        "foreshadows": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "detail": {"type": "string"}, "shown_when": {"type": "string"},
                           "trigger": {"type": "string"}, "consequence": {"type": "string"}},
            "required": ["id", "detail", "shown_when", "trigger", "consequence"],
        }},
        "details": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "shown_when": {"type": "string"}},
            "required": ["text", "shown_when"],
        }},
    },
    "required": ["intro", "places", "checkpoints", "foreshadows", "details"],
}
CLOSE_SCHEMA = {
    "type": "object",
    "properties": {"last_title": {"type": "string"}, "last_summary": {"type": "string"}, "today_summary": {"type": "string"},
                   "oneline": {"type": "string"}, "relationship": {"type": "string"}},
    "required": ["last_title", "last_summary", "today_summary", "oneline", "relationship"],
}
STAGE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
ALIVE_SCHEMA = {"type": "object", "properties": {"alive": {"type": "boolean"}}, "required": ["alive"]}
COMPRESS_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "today_summary": {"type": "string"},
                   "relationship": {"type": "string"}},
    "required": ["title", "summary", "today_summary", "relationship"],
}
NO_REVIEW = {"tension": False, "branch": None, "topics": [], "recall_small": [], "action_type": "short_talk", "move_to": None,
             "relationship_shift": None, "relationship_note": None, "parted": False, "shown_said_quote": None, "hidden_spoken": []}


def review_schema(places: list[str], topics: list[str]) -> dict:
    """장소와 화제는 닫힌 목록이다. 모델이 벗어날 수 없게 스키마에 넣는다."""
    nullable = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "tension": {"type": "boolean"}, "branch": nullable,
            "topics": {"type": "array", "items": {"type": "string", "enum": topics}},
            "recall_small": {"type": "array", "items": {"type": "string"}},
            "action_type": {"type": "string", "enum": ["short_talk", "activity", "move", "meal"]},
            "move_to": {"anyOf": [{"type": "string", "enum": places}, {"type": "null"}]},
            "relationship_shift": nullable, "relationship_note": nullable,
            "parted": {"type": "boolean"}, "shown_said_quote": nullable,
            "hidden_spoken": {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["id", "quote"],
            }},
        },
        "required": list(NO_REVIEW),
    }


def load_prompt(name: str) -> dict:
    return yaml.safe_load((PROMPTS / f"{name}.yaml").read_text(encoding="utf-8"))


def load_data(*parts: str) -> dict:
    return yaml.safe_load(DATA.joinpath(*parts).read_text(encoding="utf-8"))


def scene_problems(scene: dict) -> list[str]:
    """장면의 조건이 코드가 평가할 수 있는 형태인지 본다. 빈 목록이면 쓸 수 있다."""
    problems: list[str] = []
    places = scene.get("places") or []
    ids = {c["id"] for c in scene.get("checkpoints", [])}

    def check(owner: str, trigger) -> None:
        problems.extend(f"{owner}: {p}" for p in validate_trigger(trigger, ids))
        for place in _places_in(trigger):
            if place not in places:
                problems.append(f"{owner}: 목록에 없는 장소 {place!r}")

    if not places:
        problems.append("장소 목록이 없음")
    for c in scene.get("checkpoints", []):
        check(c["id"], c["trigger"])
    for f in scene.get("foreshadows", []):
        check(f["id"], f["trigger"])
        if f.get("shown_when") is not None:
            check(f["id"], f["shown_when"])
    for d in scene.get("details", []):
        if d.get("shown_when") is not None:
            check(d["text"], d["shown_when"])
    return problems


def _places_in(trigger) -> set[str]:
    if not isinstance(trigger, dict):
        return set()
    found: set[str] = set()
    for key, value in trigger.items():
        if key in ("all", "any") and isinstance(value, list):
            for item in value:
                found |= _places_in(item)
        elif key == "state_eq" and isinstance(value, dict) and "place" in value:
            found.add(value["place"])
    return found


@dataclass
class Judgement:
    tension: bool = False
    branch: str | None = None
    topics: list[str] = field(default_factory=list)

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

    def review(self, context: str, places: list[str], topics: list[str]) -> dict:
        raw = self.llm.json(self.prompts["review_system"], context, review_schema(places, topics), label="review")
        return {**NO_REVIEW, **raw}

    def stage(self, core: str, situation: str) -> str:
        prompt = f"[사건의 핵심]\n{core}\n\n{situation}"
        return str(self.llm.json(self.prompts["stage_system"], prompt, STAGE_SCHEMA, label="stage").get("text") or "").strip()

    def alive(self, segment: str, recent: str) -> bool:
        prompt = f"[지난 구간]\n{segment}\n\n[최근 대화]\n{recent}"
        return bool(self.llm.json(self.prompts["alive_system"], prompt, ALIVE_SCHEMA, label="alive").get("alive"))

    def compress(self, relationship: str, today: str, segment: str, events: str = "") -> dict:
        """뒤의 대화를 주지 않아 요약이 구간 밖으로 넘어갈 수 없다."""
        prompt = (f"받은 관계 설명: {relationship}\n\n[지금까지의 오늘 요약]\n{today or '없음'}\n\n"
                  f"이 구간에 세계에 일어난 일:\n{events or '없음'}\n\n정리할 구간:\n{segment}")
        return self.llm.json(self.prompts["compress_system"], prompt, COMPRESS_SCHEMA, label="compress")

    def close(self, relationship: str, today: str, remaining: str, events: str) -> dict:
        prompt = (f"받은 관계 설명: {relationship}\n\n[지금까지의 오늘 요약]\n{today or '없음'}\n\n"
                  f"세계에 일어난 일:\n{events or '없음'}\n\n아직 접지 않은 마지막 대화:\n{remaining or '없음'}")
        return self.llm.json(self.prompts["close_system"], prompt, CLOSE_SCHEMA, label="close")

    def scene_draft(self, brief: str) -> tuple[dict, list[str]]:
        """장면 초안을 받아 조건을 파싱한다. 어휘에 맞지 않으면 한 번 고쳐 받고, 남은 문제는 검수자에게 돌려준다."""
        prompt, scene, problems = brief, {}, []
        for _ in range(2):
            raw = self.llm.json(self.prompts["scene_draft_system"], prompt, SCENE_SCHEMA, label="scene_draft")
            scene = _parse_scene(raw)
            problems = scene_problems(scene)
            if not problems:
                break
            prompt = f"{brief}\n\n직전 초안의 조건에 문제가 있었다: {problems}\n어휘만 써서 전체를 다시 쓴다."
        return scene, problems


def _parse_scene(raw: dict) -> dict:
    def parse(value):
        try:
            return json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            return value

    scene = {**raw}
    scene["checkpoints"] = [{**c, "trigger": parse(c["trigger"])} for c in raw.get("checkpoints", [])]
    scene["foreshadows"] = [{**f, "trigger": parse(f["trigger"]), "shown_when": parse(f["shown_when"])} for f in raw.get("foreshadows", [])]
    scene["details"] = [{**d, "shown_when": parse(d["shown_when"])} for d in raw.get("details", [])]
    return scene
