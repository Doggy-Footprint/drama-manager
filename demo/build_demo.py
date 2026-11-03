"""이벤트 로그(runs/*.jsonl)와 채점 결과만 읽어 로그 뷰어를 만든다. 엔진 코드를 import하지 않는다.

사용: python demo/build_demo.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLE = {"gentle": "다정한 유저", "aloof": "무심한 유저", "fiery": "잘 따지는 유저"}
INLINE = {"checkpoint_fired", "checkpoint_deferred", "hidden_revealed", "hidden_spoken", "memory_recalled", "detail_planted", "detail_added",
          "payoff_injected", "payoff_unused", "detail_paid", "frame_changed", "episode_compressed", "relationship_updated",
          "plan_generated", "refused", "today_summary_updated", "detail_dropped", "move_rejected", "relationship_kept"}


def build_run(path: Path, report: dict | None, attempt: str) -> dict:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    meta = next(e for e in events if e["kind"] == "run_meta")
    card = next(e for e in events if e["kind"] == "card")
    turns: dict[int, dict] = {}
    revealed: dict[str, int] = {}
    spoken: dict[str, int] = {}
    fired: list[str] = []
    for e in events:
        kind, n = e["kind"], e["turn"]
        if kind == "hidden_revealed":
            revealed[e["id"]] = n
        if kind == "hidden_spoken":
            spoken[e["id"]] = n
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
            counts = {s: sum(1 for d in ledger if d["status"] == s) for s in ("seed", "planted", "paid")}
            counts["expired"] = sum(1 for d in ledger if d["status"] in ("expired", "dropped"))
            t["after"] = {"clock": world["clock_text"], "place": world["place"], "relationship": world["relationship"],
                          "hunger": world["hunger"], "temperature": world["temperature"], "bond": e["relationship"],
                          "today": e["today"], "todaySummary": e.get("today_summary", ""), "details": counts, "fired": list(fired),
                          "revealed": [k for k, v in revealed.items() if v <= n],
                          "spoken": [k for k, v in spoken.items() if v <= n]}
        elif kind in INLINE:
            item = {k: v for k, v in e.items() if k not in ("turn",)}
            if kind == "plan_generated":
                item = {"kind": kind, "plan": e["plan"], "checkpoints": [c["inject"] for c in e["checkpoints"]], "details": len(e["details"])}
            t["events"].append(item)
    closed = next((e for e in events if e["kind"] == "storyline_closed"), None)
    calls = [e for e in events if e["kind"] == "llm_call"]
    end = next((e for e in events if e["kind"] == "run_end"), None)
    ledger = next((e["ledger"] for e in reversed(events) if e["kind"] == "turn_end"), [])
    mode = meta.get("mode", "structured")
    label = f"{card['name']} · {STYLE[meta['style']]}" + (" · 기준선" if mode == "baseline" else "")
    return {
        "key": f"{attempt}/{path.stem}", "attempt": attempt, "label": label, "mode": mode, "name": card["name"], "persona": card["label"],
        "hidden": [{k: h[k] for k in ("id", "fact", "cover")} for h in card["hidden"]],
        "turns": [turns[k] for k in sorted(turns) if "reply" in turns[k] and "after" in turns[k]],
        "ledger": [{**{k: d[k] for k in ("id", "text", "status", "source", "planted", "paid")}, "consequence": d.get("consequence"),
                    "kind": d.get("kind", "detail")} for d in ledger],
        "revisions": [{k: e[k] for k in ("turn", "before", "after")} for e in events if e["kind"] == "today_summary_updated"],
        "bonds": [{"turn": e["turn"], "before": e.get("before"), "after": e["state"]} for e in events if e["kind"] == "relationship_updated"],
        "closed": closed and {"oneline": closed["oneline"], "relationship": closed["relationship"]},
        "ended": bool(end and end["ended"]), "cost": sum(c["cost_usd"] for c in calls), "calls": len(calls),
        "judge": (report or {}).get("judge"),
    }


def main() -> None:
    runs, models = [], {}
    for folder in sorted((ROOT / "runs").glob("try*")):
        reports = sorted(folder.glob("judge_*.json"))
        report = json.loads(reports[-1].read_text(encoding="utf-8")) if reports else {"runs": {}}
        models[folder.name] = {"model": report.get("model"), "prompt": reports[-1].stem if reports else None}
        runs += [build_run(p, report["runs"].get(p.stem), folder.name) for p in sorted(folder.glob("*.jsonl"))]
    data = json.dumps({"runs": runs, "judges": models}, ensure_ascii=False).replace("</", "<\\/")
    page = (ROOT / "demo/template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    (ROOT / "demo/page.fragment.html").write_text(page, encoding="utf-8")
    full = ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>\n{page}\n</body></html>\n')
    (ROOT / "demo/index.html").write_text(full, encoding="utf-8")
    print("demo/index.html", {r["key"]: len(r["turns"]) for r in runs}, f"{len(page) // 1024}KB")


if __name__ == "__main__":
    main()
