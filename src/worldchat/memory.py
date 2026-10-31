"""메모리: 대화에서 뽑은 사실과 에피소드 요약. 판정이 요청할 때만 꺼낸다."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .world import WARMTH_VALUES, hhmm

FACT_LABELS = {"user_clothing_warmth": "옷 보온성"}
ALLOWED_FACTS = {"user_clothing_warmth": WARMTH_VALUES}


@dataclass
class Fact:
    key: str
    value: str
    quote: str
    clock: int
    scene: str
    turn: int
    replaced: str | None = None


@dataclass
class Episode:
    index: int
    scene: str
    start_clock: int
    end_clock: int
    summary: str


class Memory:
    def __init__(self) -> None:
        self.facts: dict[str, Fact] = {}
        self.episodes: list[Episode] = []

    def store_fact(self, candidate: dict, user_text: str | None, *, clock: int, scene: str, turn: int):
        """온톨로지 값과 유저 발화 원문으로 검증한 뒤 저장한다.

        캐릭터가 잘못 말한 값이 저장되지 않도록 인용문이 유저 발화에 있어야 한다.
        반환값: (저장된 Fact 또는 None, 거절 사유 또는 None)
        """
        key, value, quote = candidate.get("key"), candidate.get("value"), candidate.get("quote", "")
        if key not in ALLOWED_FACTS or value not in ALLOWED_FACTS[key]:
            return None, f"온톨로지에 없는 값: {key}={value}"
        if not user_text or not quote or quote not in user_text:
            return None, f"유저 발화에 없는 인용: {quote!r}"
        previous = self.facts.get(key)
        if previous and previous.value == value:
            return None, "이미 저장된 값"
        fact = Fact(key, value, quote, clock, scene, turn, previous.value if previous else None)
        self.facts[key] = fact
        return fact, None

    def add_episode(self, index: int, scene: str, start_clock: int, end_clock: int, summary: str) -> Episode:
        episode = Episode(index, scene, start_clock, end_clock, summary)
        self.episodes.append(episode)
        return episode

    def retrieve(self, keys: list[str] | None) -> list[dict]:
        """상태 판정과 기억을 잇는 규칙 검색(체감 → 옷 보온성, 불꽃놀이 → 오늘 요약)."""
        found: list[dict] = []
        for key in keys or []:
            if key == "episodes":
                for ep in self.episodes:
                    found.append({"kind": "episode", "text": f"{hhmm(ep.start_clock)} {ep.scene}: {ep.summary}"})
            elif key in self.facts:
                fact = self.facts[key]
                found.append({
                    "kind": "fact",
                    "key": key,
                    "value": fact.value,
                    "text": f'{hhmm(fact.clock)} {fact.scene} 장면에서 상대가 "{fact.quote}"라고 말했다 '
                            f"({FACT_LABELS[key]}: {fact.value})",
                })
        return found

    def long_term_text(self) -> str:
        return "\n".join(f"- {hhmm(e.start_clock)} {e.scene}: {e.summary}" for e in self.episodes)

    def snapshot(self) -> dict:
        return {
            "facts": {k: asdict(v) for k, v in self.facts.items()},
            "episodes": [asdict(e) for e in self.episodes],
        }
