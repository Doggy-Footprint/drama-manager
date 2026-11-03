"""실행 기록을 네 기준으로 채점한다. 숫자로 셀 수 있는 것은 로그에서 코드로 센다.

  python eval/judge.py runs/try2/*.jsonl    # 결과는 같은 폴더의 judge_v2.json
  python eval/judge.py --dry runs/try2/*.jsonl   # Judge 없이 로그 집계만
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
JUDGE_PROMPT = "judge_v2"
PROMPT = yaml.safe_load((ROOT / f"eval/prompts/{JUDGE_PROMPT}.yaml").read_text(encoding="utf-8"))
SCHEMA = {
    "type": "object",
    "properties": {
        "a_character": {"type": "integer"}, "a_reason": {"type": "string"},
        "b_freshness": {"type": "integer"},
        "fresh_moments": {"type": "array", "items": {"type": "object", "properties": {
            "turn": {"type": "integer"}, "quote": {"type": "string"}, "why": {"type": "string"}}, "required": ["turn", "quote", "why"]}},
        "c_breaks": {"type": "array", "items": {"type": "object", "properties": {
            "turn": {"type": "integer"}, "type": {"type": "string", "enum": ["meta", "contradiction", "hidden_leak", "out_of_character", "user_puppeting"]},
            "quote": {"type": "string"}, "why": {"type": "string"}}, "required": ["turn", "type", "quote", "why"]}},
        "d_payoffs": {"type": "array", "items": {"type": "object", "properties": {
            "planted_turn": {"type": "integer"}, "paid_turn": {"type": "integer"}, "what": {"type": "string"},
            "natural": {"type": "integer"}, "in_log": {"type": "boolean"}}, "required": ["planted_turn", "paid_turn", "what", "natural", "in_log"]}},
        "summary": {"type": "string"},
    },
    "required": ["a_character", "a_reason", "b_freshness", "fresh_moments", "c_breaks", "d_payoffs", "summary"],
}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def counts(events: list[dict]) -> dict:
    def of(kind):
        return [e for e in events if e["kind"] == kind]

    end = next(iter(of("run_end")), None)
    usage = end["usage"] if end else {}
    ends = of("turn_end")
    ledger = ends[-1]["ledger"] if ends else []
    payoffs = of("payoff_injected")
    paid = of("detail_paid")
    planted_at = {d["id"]: d["planted"]["turn"] for d in ledger if d.get("planted")}
    return {
        "turns": ends[-1]["turn"] if ends else 0,
        "last_clock": ends[-1]["world"]["clock_text"] if ends else None,
        "ended_by_story": bool(end and end["ended"]),
        "plans": len(of("plan_generated")),
        "plan_triggers_rejected": sum(len(e["rejected"]) for e in of("plan_generated")),
        "checkpoints_fired": [e["id"] for e in of("checkpoint_fired")],
        "checkpoints_deferred": len(of("checkpoint_deferred")),
        "tension_turns": sum(1 for e in of("signals") if e["tension"]),
        "branches": [(e["turn"], e["branch"]) for e in of("signals") if e["branch"]],
        "hidden_revealed": [(e["turn"], e["id"]) for e in of("hidden_revealed")],
        "hidden_spoken": [(e["turn"], e["id"]) for e in of("hidden_spoken")],
        "recalled_smalls": sum(len(e["smalls"]) for e in of("memory_recalled")),
        "compressed": [(e["start"], e["end"], e["title"]) for e in of("episode_compressed")],
        "relationship_updates": len(of("relationship_updated")),
        "frames": [(e["turn"], e["axis"], e["new"]) for e in of("frame_changed")],
        "details_total": len(ledger),
        "details_shown": len(of("detail_shown")),
        "details_planted": sum(1 for d in ledger if d["status"] in ("planted", "paid")),
        "details_expired": sum(1 for d in ledger if d["status"] == "expired"),
        "payoff_injected": len(payoffs),
        "payoff_paid": len(paid),
        "payoff_gaps": [e["turn"] - planted_at[e["id"]] for e in paid if e["id"] in planted_at],
        "refused": len(of("refused")),
        "usage": usage,
    }


def transcript(events: list[dict]) -> str:
    lines, user = [], {}
    for e in events:
        if e["kind"] == "turn_start":
            user[e["turn"]] = e["user"]
        elif e["kind"] == "character_reply":
            said = user.get(e["turn"])
            if said:
                lines.append(f"T{e['turn']} 유저: {said}")
            lines.append(f"T{e['turn']} 캐릭터: {e['text']}")
    return "\n".join(lines)


def judge_prompt(events: list[dict]) -> str:
    card = next(e for e in events if e["kind"] == "card")
    revealed = {e["id"]: e["turn"] for e in events if e["kind"] == "hidden_revealed"}
    hidden = "\n".join(f"- {h['fact']} (공개 기록: {'T' + str(revealed[h['id']]) if h['id'] in revealed else '공개 안 됨'})" for h in card["hidden"])
    payoff = "\n".join(f"T{e['turn']} {e['kind']}: {e.get('fact') or e.get('quote') or e.get('id')}" for e in events
                       if e["kind"] in ("payoff_injected", "detail_paid", "payoff_unused")) or "없음"
    return (f"[공개 카드]\n{card['system']}\n\n[숨겨진 정보]\n{hidden}\n\n[떡밥 로그]\n{payoff}\n\n[대화]\n{transcript(events)}")


def judge_run(llm, path: Path) -> dict:
    events = load(path)
    result = {"counts": counts(events)}
    if llm:
        result["judge"] = llm.json(PROMPT["system"], judge_prompt(events), SCHEMA, label="judge")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    llm = None
    if not args.dry:
        from storychat.llm import GeminiJudgeLLM

        llm = GeminiJudgeLLM()
    with ThreadPoolExecutor(6) as pool:
        results = dict(zip((p.stem for p in args.paths), pool.map(lambda p: judge_run(llm, p), args.paths)))
    if llm:
        out = args.paths[0].parent / f"{JUDGE_PROMPT}.json"
        out.write_text(json.dumps({"model": llm.model, "runs": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, r in results.items():
        c = r["counts"]
        line = (f"{name}: {c['turns']}턴 {c['last_clock']} 체크포인트{len(c['checkpoints_fired'])} 긴장{c['tension_turns']} "
                f"reveal{len(c['hidden_revealed'])} 말함{len(c['hidden_spoken'])} 살포{c['details_planted']}/{c['details_total']} "
                f"회수{c['payoff_paid']}/{c['payoff_injected']} 압축{len(c['compressed'])} 거절{c['refused']}")
        if "judge" in r:
            j = r["judge"]
            line += f" | a={j['a_character']} b={j['b_freshness']} c={len(j['c_breaks'])} d={len(j['d_payoffs'])}"
        print(line)
    all_counts = [r["counts"] for r in results.values()]
    gaps = [g for c in all_counts for g in c["payoff_gaps"]]
    print(f"전체 d: 주입 {sum(c['payoff_injected'] for c in all_counts)}, 회수 {sum(c['payoff_paid'] for c in all_counts)}, 살포→회수 턴 {gaps}")


if __name__ == "__main__":
    main()
