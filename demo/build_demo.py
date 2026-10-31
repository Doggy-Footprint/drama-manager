"""이벤트 로그(runs/*.jsonl)만 읽어 턴별 데모 HTML을 만든다. 엔진 코드를 import하지 않는다.

사용: python demo/build_demo.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = [
    ("tsundere_confession", "서진 · 고백", "츠데레. 고백이 있어 불꽃을 미룬다"),
    ("warm_confession", "도윤 · 고백", "다정함. 같은 세계, 다른 말투. 12시에 유저가 침묵한다"),
    ("warm_ordinary", "도윤 · 평범", "평범한 대화라 불꽃이 제시간에 터진다"),
]
SCORES_FILE = ROOT / "eval/reports/v4_v4_{split}.json"


def build_run(path: Path) -> dict:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    meta = next(e for e in events if e["kind"] == "run_meta")
    persona = next(e for e in events if e["kind"] == "persona")
    turns: dict[int, dict] = {}
    world = {"felt": "쾌적", "hunger": "없음", "temperature": "따뜻함", "coat": "none", "fireworks": "pending"}
    facts: dict[str, dict] = {}
    fact_turn: dict[str, int] = {}
    episodes: list[dict] = []
    in_prompt_from = 1

    for e in events:
        n = e["turn"]
        if n == 0:
            continue
        t = turns.setdefault(n, {"turn": n, "changes": [], "recalled": [], "stored": [], "rejected": []})
        kind = e["kind"]
        if kind == "turn_start":
            t.update(user=e["user"], idle=e["idle"])
        elif kind == "state_changed":
            if "reply" in t:
                t.setdefault("after_changes", []).append({k: e[k] for k in ("field", "from", "to")})
            else:
                t["changes"].append({k: e[k] for k in ("field", "from", "to")})
                world[e["field"]] = e["to"]
        elif kind == "directive":
            t["directive"] = e["name"]
            t["directive_text"] = e["text"]
        elif kind == "drama_decision":
            t["drama"] = {"action": e["action"], "reason": e["reason"], "judgement": e["judgement"]}
            if e["action"] == "defer":
                world["fireworks"] = "deferred"
        elif kind == "episode_closed":
            episodes.append({"scene": e["scene"], "summary": e["summary"], "start": e["start"], "end": e["end"], "turn": n})
            t["episode_closed"] = episodes[-1]
            in_prompt_from = n - 1
        elif kind == "memory_retrieved":
            t["recalled"] = e["items"]
        elif kind == "prompt_assembled":
            t["situation"] = e["situation"]
            match = re.search(r"시각: (\d\d:\d\d) / 장면: (\S+)", e["situation"])
            t["clock"], t["scene"] = match.group(1), match.group(2)
            t["world"] = dict(world)
            t["in_prompt_from"] = max(in_prompt_from, 1)
            t["facts_before"] = [dict(v) for v in facts.values()]
            t["episodes_before"] = list(episodes)
        elif kind == "character_reply":
            t["reply"] = e["text"]
        elif kind == "analysis":
            t["analysis"] = {k: e.get(k) for k in ("action_type", "facts", "events")}
        elif kind == "memory_stored":
            item = {k: e[k] for k in ("key", "value", "quote", "clock", "scene", "replaced")}
            item["turn"] = n
            t["stored"].append(item)
            facts[e["key"]] = item
            fact_turn[e["key"]] = n
        elif kind == "memory_rejected":
            t["rejected"].append(e["reason"])
        elif kind == "clock_advanced":
            t["minutes"] = e["minutes"]
        elif kind == "turn_end":
            for key in ("felt", "hunger", "temperature", "coat", "fireworks"):
                world[key] = e["world"][key]
            t["end_clock"] = e["world"]["clock_text"]

    end = next(e for e in events if e["kind"] == "run_end")
    ordered = [turns[k] for k in sorted(turns) if "reply" in turns[k]]
    return {
        "persona": {"name": persona["name"], "label": persona["label"], "text": persona["text"]},
        "meta": {k: meta[k] for k in ("model", "character_prompt", "analyst_prompt", "user_source")},
        "usage": end["usage"],
        "turns": ordered,
    }


def main() -> None:
    runs = {}
    for name, label, note in RUNS:
        run = build_run(ROOT / "runs" / f"{name}.jsonl")
        run.update(label=label, note=note)
        runs[name] = run
    scores = {}
    for split in ("dev", "holdout"):
        summary = json.loads(Path(str(SCORES_FILE).format(split=split)).read_text(encoding="utf-8"))["summary"]
        scores[split] = {"pass": summary["reply_pass"], "samples": summary["samples"]}
    data = json.dumps({"runs": runs, "order": [r[0] for r in RUNS], "scores": scores}, ensure_ascii=False)
    fragment = (ROOT / "demo/template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    (ROOT / "demo/page.fragment.html").write_text(fragment, encoding="utf-8")
    full = f'<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>\n{fragment}\n</body></html>\n'
    (ROOT / "demo/index.html").write_text(full, encoding="utf-8")
    print("demo/index.html", {k: len(v["turns"]) for k, v in runs.items()}, scores)


if __name__ == "__main__":
    main()
