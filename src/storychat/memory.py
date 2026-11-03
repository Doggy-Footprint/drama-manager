"""기억: 계층 에피소드, 관계 기억, 떡밥 원장. 캐릭터는 이것을 직접 들고 있지 않는다."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

KEEP_RAW_TURNS = 10      # 이만큼 지나야 '지나간 이야기' 후보가 된다
RAW_BUDGET_TURNS = 24    # 이보다 원문이 길면 살아 있어도 가장 오래된 구간을 압축한다
ALIVE_RECHECK_TURNS = 5


def squash(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "")


def quoted(quote: str, source: str) -> bool:
    """인용이 원문에 있는가. 띄어쓰기와 문장부호 차이는 무시한다."""
    q = squash(quote)
    return len(q) >= 2 and q in squash(source)


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
        self.today = BigEpisode("today", "오늘 놀이동산 데이트", "지금 이어지고 있는 오늘의 데이트")
        self.bigs["today"] = self.today

    def index_text(self) -> str:
        return "\n".join(f"- {b.id}: {b.title}. {b.oneline}" for b in self.bigs.values()
                         if b.id != "today" or b.smalls)

    def smalls_text(self, big_id: str) -> str:
        return "\n".join(f"- {s.id}: {s.title}" for s in self.bigs[big_id].smalls)

    def small(self, small_id: str) -> SmallEpisode | None:
        return next((s for b in self.bigs.values() for s in b.smalls if s.id == small_id), None)

    def add_today(self, title: str, summary: str, turns: tuple[int, int]) -> SmallEpisode:
        episode = SmallEpisode(f"today-{len(self.today.smalls) + 1}", title, summary, turns)
        self.today.smalls.append(episode)
        return episode

    def today_titles(self) -> list[str]:
        return [s.title for s in self.today.smalls]


class RelationshipMemory:
    """에피소드가 '무슨 일'이라면 이것은 '그래서 우리 사이가 어떤가'다. 압축할 때 사후 갱신한다."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.history: list[dict] = []

    def update(self, note: str, episode_id: str, turn: int) -> None:
        self.state = note
        self.history.append({"turn": turn, "episode": episode_id, "state": note})


@dataclass
class Detail:
    id: str
    text: str
    source: str = "plan"  # plan | user | character
    status: str = "seed"  # seed → planted → paid | expired
    affordances: list[str] = field(default_factory=list)
    plan_id: str | None = None
    shown: int = 0
    planted: dict | None = None
    paid: dict | None = None


class DetailLedger:
    MAX_SHOWN = 2  # 두 번 보여 줘도 말하지 않으면 다음 디테일로 넘어간다

    def __init__(self) -> None:
        self.items: dict[str, Detail] = {}
        self._next = 1

    def _new_id(self) -> str:
        detail_id = f"d{self._next}"
        self._next += 1
        return detail_id

    def add_seeds(self, seeds: list[dict], plan_id: str) -> list[Detail]:
        added = []
        for seed in seeds:
            detail = Detail(self._new_id(), seed["text"], affordances=list(seed.get("affordances", [])), plan_id=plan_id)
            self.items[detail.id] = detail
            added.append(detail)
        return added

    def add_spoken(self, text: str, source: str, affordances: list[str], quote: str, turn: int) -> Detail:
        detail = Detail(self._new_id(), text, source=source, status="planted", affordances=affordances,
                        planted={"turn": turn, "quote": quote})
        self.items[detail.id] = detail
        return detail

    def next_seed(self) -> Detail | None:
        return next((d for d in self.items.values() if d.status == "seed" and d.shown < self.MAX_SHOWN), None)

    def plant(self, detail_id: str, quote: str, turn: int) -> None:
        detail = self.items[detail_id]
        detail.status, detail.planted = "planted", {"turn": turn, "quote": quote}

    def pay(self, detail_id: str, how: str, turn: int) -> None:
        detail = self.items[detail_id]
        detail.status, detail.paid = "paid", {"turn": turn, "how": how}

    def planted(self) -> list[Detail]:
        return [d for d in self.items.values() if d.status == "planted"]

    def expire_seeds(self) -> list[str]:
        """프레임 플랜이 닫히면 뿌리지 못한 씨앗은 그냥 사라진다. 살포된 것은 남긴다."""
        gone = []
        for detail in self.items.values():
            if detail.status == "seed":
                detail.status = "expired"
                gone.append(detail.id)
        return gone

    def snapshot(self) -> list[dict]:
        return [asdict(d) for d in self.items.values()]
