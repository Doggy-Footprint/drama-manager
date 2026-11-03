"""이벤트 로그(runs/*.jsonl)와 채점 결과만 읽어 로그 뷰어를 만든다. 엔진 코드를 import하지 않는다.

사용: python demo/build_demo.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLE = {"gentle": "다정한 유저", "aloof": "무심한 유저", "fiery": "잘 따지는 유저"}
INLINE = {"checkpoint_fired", "checkpoint_deferred", "hidden_revealed", "memory_recalled", "detail_planted", "detail_added",
          "payoff_injected", "payoff_unused", "detail_paid", "frame_changed", "episode_compressed", "relationship_updated",
          "plan_generated", "refused"}


def build_run(path: Path, report: dict | None) -> dict:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    meta = next(e for e in events if e["kind"] == "run_meta")
    card = next(e for e in events if e["kind"] == "card")
    turns: dict[int, dict] = {}
    revealed: dict[str, int] = {}
    fired: list[str] = []
    for e in events:
        kind, n = e["kind"], e["turn"]
        if kind == "hidden_revealed":
            revealed[e["id"]] = n
        if kind == "checkpoint_fired":
            fired.append(e["id"])
        if n == 0:
            continue
        t = turns.setdefault(n, {"turn": n, "events": []})
        if kind == "turn_start":
            t.update(user=e["user"], clock=e["clock"], place=e["place"])
        elif kind == "character_reply":
            t["reply"] = e["text"]
        elif kind == "signals":
            t["tension"], t["branch"], t["topics"] = e["tension"], e["branch"], e["topics"]
        elif kind == "prompt_assembled":
            t["moment"] = e["moment"]
        elif kind == "clock_advanced":
            t["action"], t["minutes"] = e["action"], e["minutes"]
        elif kind == "turn_end":
            world, ledger = e["world"], e["ledger"]
            counts = {s: sum(1 for d in ledger if d["status"] == s) for s in ("seed", "planted", "paid", "expired")}
            t["after"] = {"clock": world["clock_text"], "place": world["place"], "relationship": world["relationship"],
                          "hunger": world["hunger"], "temperature": world["temperature"], "bond": e["relationship"],
                          "today": e["today"], "details": counts, "fired": list(fired),
                          "revealed": [k for k, v in revealed.items() if v <= n]}
        elif kind in INLINE:
            item = {k: v for k, v in e.items() if k not in ("turn",)}
            if kind == "plan_generated":
                item = {"kind": kind, "plan": e["plan"], "checkpoints": [c["inject"] for c in e["checkpoints"]], "details": len(e["details"])}
            t["events"].append(item)
    closed = next((e for e in events if e["kind"] == "storyline_closed"), None)
    calls = [e for e in events if e["kind"] == "llm_call"]
    end = next((e for e in events if e["kind"] == "run_end"), None)
    ledger = next((e["ledger"] for e in reversed(events) if e["kind"] == "turn_end"), [])
    return {
        "key": path.stem, "label": f"{card['name']} · {STYLE[meta['style']]}", "name": card["name"], "persona": card["label"],
        "hidden": [{k: h[k] for k in ("id", "fact", "cover")} for h in card["hidden"]],
        "turns": [turns[k] for k in sorted(turns) if "reply" in turns[k] and "after" in turns[k]],
        "ledger": [{k: d[k] for k in ("id", "text", "status", "source", "planted", "paid")} for d in ledger],
        "closed": closed and {"oneline": closed["oneline"], "relationship": closed["relationship"]},
        "ended": bool(end and end["ended"]), "cost": sum(c["cost_usd"] for c in calls), "calls": len(calls),
        "judge": (report or {}).get("judge"),
    }


def main() -> None:
    report_file = ROOT / "eval/reports/judge_v1.json"
    report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else {"runs": {}}
    runs = [build_run(p, report["runs"].get(p.stem)) for p in sorted((ROOT / "runs").glob("*.jsonl"))]
    data = json.dumps({"runs": runs, "judge_model": report.get("model")}, ensure_ascii=False).replace("</", "<\\/")
    page = (ROOT / "demo/template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    (ROOT / "demo/page.fragment.html").write_text(page, encoding="utf-8")
    full = ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>\n{page}\n</body></html>\n')
    (ROOT / "demo/index.html").write_text(full, encoding="utf-8")
    print("demo/index.html", {r["key"]: len(r["turns"]) for r in runs}, f"{len(page) // 1024}KB")


if __name__ == "__main__":
    main()
