"""try5 실행 로그로 사람 평가 페이지를 만든다. 판단 결과와 조건은 페이지에 넣지 않는다.

  python eval/review_build.py runs/try5      # eval/review/page.html, runs/try5/blind_map.json

블라인드 비교의 A~D 대응표는 runs/try5/blind_map.json에만 있고 페이지에는 없다.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 5
LABEL_KEY_TURNS = 12
LABEL_RANDOM_TURNS = 8


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def dialogue(events: list[dict]) -> dict[int, dict]:
    turns: dict[int, dict] = {}
    for e in events:
        if e["kind"] == "turn_start":
            turns.setdefault(e["turn"], {})["u"] = e["user"]
        elif e["kind"] == "character_reply":
            turns.setdefault(e["turn"], {})["c"] = e["text"]
    return {n: {"turn": n, "u": t.get("u"), "c": t.get("c", "")} for n, t in sorted(turns.items()) if "c" in t}


def label_items(name: str, events: list[dict], rng: random.Random, run_no: int) -> list[dict]:
    turns = dialogue(events)
    key: list[int] = []
    for e in events:
        if e["kind"] in ("hidden_spoken", "relationship_updated", "frame_changed") and e["turn"] in turns:
            key.append(e["turn"])
        if e["kind"] == "hidden_revealed" and e["turn"] - 1 in turns:
            key.append(e["turn"] - 1)  # 열리게 만든 판단은 그 전 턴의 것이다
        if e["kind"] == "review" and e.get("parted"):
            key.append(e["turn"])
    last = max(turns)
    key += [last, last - 1]
    picked = list(dict.fromkeys(t for t in key if t in turns))[:LABEL_KEY_TURNS // 2]
    rest = [t for t in turns if t not in picked and t > 1]
    picked += rng.sample(rest, min(LABEL_RANDOM_TURNS // 2, len(rest)))
    items = []
    for t in sorted(picked):
        context = [turns[k] for k in range(max(1, t - 2), t) if k in turns]
        items.append({"id": f"{name}-T{t}", "run": f"실행 {run_no}", "turn": t, "context": context, "cur": turns[t]})
    return items


def check_items(name: str, events: list[dict], run_no: int) -> list[dict]:
    turns = dialogue(events)
    run = f"실행 {run_no}"
    reply = lambda t: turns.get(t, {}).get("c")
    items = []
    for e in events:
        if e["kind"] == "checkpoint_fired" or e["kind"] == "payoff_injected":
            kind = "event" if e["kind"] == "checkpoint_fired" else "payoff"
            items.append({"id": f"{name}-{kind}-{e['id']}", "kind": kind, "run": run, "turn": e["turn"],
                          "text": e.get("inject") or e.get("fact"), "u": turns.get(e["turn"], {}).get("u"),
                          "c": reply(e["turn"]), "next": turns.get(e["turn"] + 1)})
        if e["kind"] == "relationship_updated":
            items.append({"id": f"{name}-bond-T{e['turn']}", "kind": "bond", "run": run, "turn": e["turn"],
                          "before": e.get("before"), "after": e["state"],
                          "later": [turns[k] for k in range(e["turn"] + 1, e["turn"] + 4) if k in turns]})
        if e["kind"] == "today_summary_updated":
            items.append({"id": f"{name}-today-T{e['turn']}", "kind": "today", "run": run, "turn": e["turn"],
                          "before": e["before"], "after": e["after"]})
    payoffs = {e["id"]: e for e in events if e["kind"] == "detail_paid"}
    planted = {e["id"]: e for e in events if e["kind"] == "detail_planted"}
    for item in items:
        if item["kind"] == "payoff":
            detail_id = item["id"].split("-payoff-")[1]
            p = planted.get(detail_id)
            item["planted"] = p and {"turn": p["turn"], "quote": p["quote"]}
            item["gap"] = payoffs.get(detail_id, {}).get("gap")
    return items


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs/try5"
    paths = sorted(folder.glob("*.jsonl"))
    runs = {p.stem: load(p) for p in paths}
    rng = random.Random(SEED)
    letters = list("ABCD")[:len(runs)]
    order = list(runs)
    rng.shuffle(order)
    blind = dict(zip(letters, order))
    (folder / "blind_map.json").write_text(json.dumps(blind, ensure_ascii=False, indent=1), encoding="utf-8")
    compare = [{"letter": letter, "turns": list(dialogue(runs[name]).values())} for letter, name in blind.items()]
    structured = [n for n in sorted(runs) if n.startswith("structured")]
    labels, checks = [], []
    for i, name in enumerate(structured, 1):
        labels += label_items(name, runs[name], rng, i)
        checks += check_items(name, runs[name], i)
    card = next(e for e in next(iter(runs.values())) if e["kind"] == "card")
    scene = next(e for e in next(iter(runs.values())) if e["kind"] == "scene")
    data = {"compare": compare, "labels": labels, "checks": checks, "places": scene["places"],
            "topics": ["놀이기구", "둘의 장래", "영화", "우리 관계", "음식", "지난 추억", "회사 일"],
            "hidden": [{"id": h["id"], "fact": h["fact"]} for h in card["hidden"]]}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = (ROOT / "eval/review/template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    out = ROOT / "eval/review/page.html"
    out.write_text(page, encoding="utf-8")
    print(out, {"compare": len(compare), "labels": len(labels), "checks": len(checks)}, f"{len(page) // 1024}KB")


if __name__ == "__main__":
    main()
