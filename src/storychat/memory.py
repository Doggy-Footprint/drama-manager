"""기억: 계층 에피소드, 오늘 요약, 관계 기억, 준비된 디테일의 원장. 캐릭터는 이것을 직접 들고 있지 않는다."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

KEEP_RAW_TURNS = 6       # 이만큼 지나야 '지나간 이야기' 후보가 된다
MIN_SEGMENT_TURNS = 3     # 작은 사건마다 조각나지 않도록 에피소드의 최소 길이
RAW_BUDGET_TURNS = 24    # 이보다 원문이 길면 살아 있어도 가장 오래된 구간을 압축한다
ALIVE_RECHECK_TURNS = 5


GESTURE = re.compile(r"\([^)]*\)")


def squash(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "")


def quoted(quote: str, source: str) -> bool:
    """인용이 원문에 있는가. 띄어쓰기, 문장부호, 사이에 끼어든 괄호 몸짓의 차이는 무시한다."""
    q = squash(GESTURE.sub("", quote or ""))
    return len(q) >= 2 and (q in squash(source) or q in squash(GESTURE.sub("", source or "")))


@dataclass
class SmallEpisode:
    id: str
    title: str
    summary: str
    turns: tuple[int, int] | None = None


@dataclass
class BigEpisode:
    id: str
    title: str
    oneline: str
    smalls: list[SmallEpisode] = field(default_factory=list)


class EpisodeStore:
    def __init__(self, seed: list[dict]) -> None:
        self.bigs: dict[str, BigEpisode] = {}
        for big in seed:
            smalls = [SmallEpisode(**s) for s in big.get("smalls", [])]
            self.bigs[big["id"]] = BigEpisode(big["id"], big["title"], big["oneline"], smalls)
        self.today = BigEpisode("today", "오늘 놀이동산", "지금 이어지고 있는 오늘")
        self.bigs["today"] = self.today
        self.today_summary = ""
        self.revisions: list[dict] = []  # 오늘 요약이 어떻게 고쳐졌는지. 지나간 작은 에피소드는 고치지 않는다

    def small_index(self) -> str:
        return "\n".join(f"- {s.id}: {b.title} / {s.title}" for b in self.bigs.values() for s in b.smalls)

    def small(self, small_id: str) -> SmallEpisode | None:
        return next((s for b in self.bigs.values() for s in b.smalls if s.id == small_id), None)

    def add_today(self, title: str, summary: str, turns: tuple[int, int]) -> SmallEpisode:
        episode = SmallEpisode(f"today-{len(self.today.smalls) + 1}", title, summary, turns)
        self.today.smalls.append(episode)
        return episode

    def today_titles(self) -> list[str]:
        return [s.title for s in self.today.smalls]

    def revise_today(self, text: str, turn: int) -> dict:
        revision = {"turn": turn, "before": self.today_summary, "after": text}
        self.today_summary = text
        self.revisions.append(revision)
        return revision


class RelationshipMemory:
    """에피소드가 '무슨 일'이라면 이것은 '그래서 우리 사이가 어떤가'다. 사이가 바뀐 턴과 압축할 때 갱신한다."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.history: list[dict] = []

    def update(self, note: str, source: str, turn: int) -> dict:
        change = {"turn": turn, "source": source, "before": self.state, "state": note}
        self.state = note
        self.history.append(change)
        return change

    def changed_since(self, turn: int) -> bool:
        """그 턴 이후 대화 중에 사이가 바뀌었는가. 압축이 쓴 갱신은 세지 않는다."""
        return any(h["turn"] >= turn and h["source"] == "turn" for h in self.history)


@dataclass
class Detail:
    id: str
    text: str
    kind: str = "detail"  # detail | foreshadow
    source: str = "scene"
    status: str = "seed"  # seed → planted → paid. 살포 전에 결과 조건이 맞으면 dropped
    shown_when: dict | None = None
    trigger: dict | None = None
    consequence: str | None = None
    shown: int = 0
    planted: dict | None = None
    paid: dict | None = None
    dropped: dict | None = None


class DetailLedger:
    """장면에 준비된 것만 추적한다. 대화에서 새 디테일을 추가하는 길은 없다(지어낸 과거가 저장되는 경로)."""
    MAX_SHOWN = 2  # 두 번 보여 줘도 말하지 않으면 다음 디테일로 넘어간다

    def __init__(self, scene: dict) -> None:
        self.items: dict[str, Detail] = {}
        for item in scene.get("foreshadows", []):
            self.items[item["id"]] = Detail(item["id"], item["detail"], "foreshadow", shown_when=item.get("shown_when"),
                                            trigger=item["trigger"], consequence=item["consequence"])
        for i, item in enumerate(scene.get("details", []), 1):
            detail_id = item.get("id", f"detail_{i}")
            self.items[detail_id] = Detail(detail_id, item["text"], shown_when=item.get("shown_when"))

    def showable(self) -> list[Detail]:
        return [d for d in self.items.values() if d.status == "seed" and d.shown < self.MAX_SHOWN]

    def foreshadows(self) -> list[Detail]:
        return [d for d in self.items.values() if d.kind == "foreshadow"]

    def plant(self, detail_id: str, quote: str, turn: int) -> None:
        detail = self.items[detail_id]
        detail.status, detail.planted = "planted", {"turn": turn, "quote": quote}

    def pay(self, detail_id: str, turn: int) -> None:
        detail = self.items[detail_id]
        detail.status, detail.paid = "paid", {"turn": turn, "how": "world"}

    def drop(self, detail_id: str, turn: int) -> None:
        detail = self.items[detail_id]
        detail.status, detail.dropped = "dropped", {"turn": turn}

    def planted(self) -> list[Detail]:
        return [d for d in self.items.values() if d.status == "planted"]

    def snapshot(self) -> list[dict]:
        return [asdict(d) for d in self.items.values()]
