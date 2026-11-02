"""multi-turn golden 평가 (teacher forcing).

한 턴 채점이 끝나면 모델 응답을 버리고 정답 응답·정답 분석으로 다음 턴을 시작한다.
따라서 앞 턴의 실수가 뒤 턴 점수로 번지지 않는다.

사용:
  python eval/golden.py --character v1 --split dev --samples 3
  python eval/golden.py --calibrate
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldchat import world as w  # noqa: E402
from worldchat.drama import DramaJudgement  # noqa: E402
from worldchat.engine import Engine, TurnContext  # noqa: E402
from worldchat.llm import GeminiLLM, ScriptedLLM  # noqa: E402
from worldchat.memory import Fact  # noqa: E402

PERSONAS = ("warm", "tsundere")
JUDGE = yaml.safe_load((ROOT / "eval/prompts/judge_v1.yaml").read_text(encoding="utf-8"))
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "pass": {"type": "boolean"}, "why": {"type": "string"}},
                "required": ["id", "pass", "why"],
            },
        }
    },
    "required": ["items"],
}
CLOCK_FIELDS = {"last_meal", "clock", "meal_proposed_at"}


def load_cases(split: str) -> list[dict]:
    return yaml.safe_load((ROOT / "golden" / f"{split}.yaml").read_text(encoding="utf-8"))


def force_start(engine: Engine, case: dict) -> None:
    state = engine.state
    start = case.get("start")
    if start:
        state.turn = 5  # 무언의 턴이 오프닝으로 처리되지 않게 한다
    for key, value in (start or {}).items():
        setattr(state, key, w.parse_hhmm(value) if key in CLOCK_FIELDS else value)
    for f in case.get("facts", []):
        engine.memory.facts[f["key"]] = Fact(f["key"], f["value"], f["quote"], w.parse_hhmm(f["clock"]), f["scene"], 1)
    for i, ep in enumerate(case.get("episodes", [])):
        engine.memory.add_episode(i, ep["scene"], w.parse_hhmm(ep["start"]), w.parse_hhmm(ep["end"]), ep["summary"])
    engine.episode_start = state.clock


def check_world(engine: Engine, ctx: TurnContext, expect: dict) -> list[str]:
    """코드 판정 검사. LLM과 무관하므로 하나라도 틀리면 프롬프트가 아니라 코드 버그다."""
    errors = []
    for key, want in expect.items():
        if key == "directive":
            got = ctx.plan.directive
        elif key == "clock":
            got = w.hhmm(engine.state.clock)
        elif key == "recalled":
            got = want if any(want in (i.get("key", "episodes") if i["kind"] == "fact" else "episodes")
                              for i in ctx.recalled) else None
        else:
            got = getattr(engine.state, key)
        if got != want:
            errors.append(f"{key}: 기대 {want!r}, 실제 {got!r}")
    return errors


def regex_checks(reply: str, checks: dict) -> list[str]:
    failures = []
    if "유저" in reply:
        failures.append("메타 단어 노출: 유저")
    for field in ("must_any", "must_any_2"):
        if field in checks and not any(re.search(p, reply) for p in checks[field]):
            failures.append(f"{field} 없음: {checks[field]}")
    for pattern in checks.get("must_not", []):
        if re.search(pattern, reply):
            failures.append(f"must_not 위반: {pattern}")
    return failures


def judge_reply(llm, persona: dict, ctx_text: str, reply: str, directive: str | None, extra: list[str]) -> dict:
    items = {"no_hijack": JUDGE["no_hijack"], "in_character": JUDGE["in_character"].format(persona=persona["text"])}
    items["format"] = JUDGE["format"]
    for i, text in enumerate(extra):
        items[f"case_{i}"] = text
    listing = "\n".join(f"- {k}: {v}" for k, v in items.items())
    prompt = f"[상황과 직전 대화]\n{ctx_text}\n\n[채점할 캐릭터 응답]\n{reply}\n\n[항목]\n{listing}"
    result = llm.json(JUDGE["system"], [{"role": "user", "text": prompt}], JUDGE_SCHEMA, label="judge")
    return {i["id"]: {"pass": bool(i["pass"]), "why": i["why"]} for i in result.get("items", [])}


def grade_analysis(got: dict, gold: dict) -> list[str]:
    errors = []
    allowed = gold["action_type"] if isinstance(gold["action_type"], list) else [gold["action_type"]]
    if got.get("action_type") not in allowed:
        errors.append(f"action_type: 기대 {gold['action_type']}, 실제 {got.get('action_type')}")
    got_facts = {(f["key"], f["value"]) for f in got.get("facts", [])}
    gold_facts = {(f["key"], f["value"]) for f in gold["facts"]}
    if got_facts != gold_facts:
        errors.append(f"facts: 기대 {sorted(gold_facts)}, 실제 {sorted(got_facts)}")
    if set(got.get("events", [])) - set(gold.get("events_ok", [])) != set(gold["events"]):
        errors.append(f"events: 기대 {gold['events']}, 실제 {got.get('events')}")
    return errors


def context_text(ctx: TurnContext) -> str:
    lines = [f"{'유저' if m['role'] == 'user' else '캐릭터'}: {m['text']}" for m in ctx.messages]
    return "\n".join(lines)


def run_case(case: dict, persona_name: str, args, llm, judge_llm, character: str) -> list[dict]:
    forced_llm = ScriptedLLM({})
    engine = Engine(forced_llm, persona_name, character_version=character, analyst_version=args.analyst)
    force_start(engine, case)
    rows = []
    for index, turn in enumerate(case["turns"]):
        drama = DramaJudgement(**turn["drama"]) if turn.get("drama") else None
        needs_drama = w.FIREWORKS_AT <= engine.state.clock and engine.state.fireworks != "fired"
        if needs_drama and drama is None:
            drama = DramaJudgement()
        ctx = engine.prepare(turn["user"], drama=drama, summary=turn.get("summary", "(요약 없음)"))
        world_errors = check_world(engine, ctx, turn.get("expect", {}))
        gold_reply = turn["gold"][persona_name]

        row = {"case": case["id"], "turn": index, "persona": persona_name, "user": turn["user"],
               "directive": ctx.plan.directive, "world_errors": world_errors, "samples": []}

        for _ in range(args.samples):
            reply = llm.text(ctx.system, ctx.messages, label="character")
            regex = regex_checks(reply, turn.get("checks", {}))
            judged = judge_reply(judge_llm, engine.persona, context_text(ctx), reply, ctx.plan.directive,
                                 turn.get("checks", {}).get("judge", []))
            failed = [f"{k}: {v['why']}" for k, v in judged.items() if not v["pass"]]
            row["samples"].append({"reply": reply, "regex": regex, "judge_fail": failed,
                                   "ok": not regex and not failed})

        analysis_llm_engine = Engine(llm, persona_name, character_version=character, analyst_version=args.analyst)
        analysis_llm_engine.state = engine.state
        got = analysis_llm_engine.analyze(ctx, gold_reply)
        row["analysis_errors"] = grade_analysis(got, turn["analysis"]) if turn.get("analysis") else []

        if turn.get("drama") and ctx.user_text is not None:
            drama_engine = Engine(llm, persona_name, character_version=character, analyst_version=args.analyst)
            drama_engine.history = list(engine.history)
            judged = drama_engine.judge_drama(ctx.user_text)
            want = DramaJudgement(**turn["drama"])
            row["drama_errors"] = [] if (judged.important == want.important and judged.peak == want.peak) else [
                f"기대 important={want.important},peak={want.peak} 실제 important={judged.important},peak={judged.peak}"]
        else:
            row["drama_errors"] = []

        analysis = dict(turn.get("analysis") or {"action_type": "short_talk", "facts": [], "events": []})
        analysis["facts"] = [{**f, "quote": turn["user"] or ""} for f in analysis.get("facts", [])]
        if isinstance(analysis["action_type"], list):
            analysis["action_type"] = analysis["action_type"][0]
        engine.finish(ctx, gold_reply, analysis)  # teacher forcing: 정답으로 다음 턴을 시작한다
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    samples = [s for r in rows for s in r["samples"]]
    per_persona = {}
    for persona in PERSONAS:
        ss = [s for r in rows if r["persona"] == persona for s in r["samples"]]
        per_persona[persona] = round(sum(s["ok"] for s in ss) / max(len(ss), 1), 3)
    per_case: dict[str, list] = {}
    for r in rows:
        per_case.setdefault(r["case"], []).extend(s["ok"] for s in r["samples"])
    return {
        "reply_pass": round(sum(s["ok"] for s in samples) / max(len(samples), 1), 3),
        "samples": len(samples),
        "per_persona": per_persona,
        "per_case": {k: round(sum(v) / max(len(v), 1), 2) for k, v in per_case.items()},
        "world_errors": sum(len(r["world_errors"]) for r in rows),
        "analysis_errors": sum(len(r["analysis_errors"]) for r in rows),
        "analysis_turns": len(rows),
        "drama_errors": sum(len(r["drama_errors"]) for r in rows),
    }


def print_failures(rows: list[dict]) -> None:
    for r in rows:
        tag = f"{r['case']}#{r['turn']} {r['persona']} [{r['directive']}]"
        for err in r["world_errors"]:
            print(f"WORLD {tag}: {err}")
        for err in r["analysis_errors"]:
            print(f"ANALYSIS {tag}: {err}")
        for err in r["drama_errors"]:
            print(f"DRAMA {tag}: {err}")
        for s in r["samples"]:
            if not s["ok"]:
                print(f"REPLY {tag}\n  유저: {r['user']}\n  응답: {s['reply']}\n  regex: {s['regex']}\n  judge: {s['judge_fail']}")


def calibrate(args) -> None:
    """Judge 보정: gold는 통과해야 하고 bad는 떨어져야 한다. 지표는 fail recall이다."""
    judge_llm = GeminiLLM(args.model)
    jobs = []
    for split in ("dev", "holdout"):
        for case in load_cases(split):
            for persona in PERSONAS:
                forced = Engine(ScriptedLLM({}), persona, character_version=args.character, analyst_version=args.analyst)
                force_start(forced, case)
                for index, turn in enumerate(case["turns"]):
                    drama = DramaJudgement(**turn["drama"]) if turn.get("drama") else None
                    if drama is None and forced.state.clock >= w.FIREWORKS_AT and forced.state.fireworks != "fired":
                        drama = DramaJudgement()
                    ctx = forced.prepare(turn["user"], drama=drama, summary=turn.get("summary", "(요약 없음)"))
                    extra = turn.get("checks", {}).get("judge", [])
                    text = context_text(ctx)
                    jobs.append((case["id"], index, persona, "gold", turn["gold"][persona], text, extra, turn.get("checks", {})))
                    if turn.get("bad"):
                        jobs.append((case["id"], index, persona, "bad", turn["bad"][persona], text, extra, turn.get("checks", {})))
                    analysis = dict(turn.get("analysis") or {"action_type": "short_talk", "facts": [], "events": []})
                    analysis["facts"] = [{**f, "quote": turn["user"] or ""} for f in analysis.get("facts", [])]
                    if isinstance(analysis["action_type"], list):
                        analysis["action_type"] = analysis["action_type"][0]
                    forced.finish(ctx, turn["gold"][persona], analysis)

    def run(job):
        case, index, persona, kind, reply, text, extra, checks = job
        judged = judge_reply(judge_llm, {"text": Engine(ScriptedLLM({}), persona, character_version=args.character,
                                                        analyst_version=args.analyst).persona["text"]},
                             text, reply, None, extra)
        fails = [f"{k}: {v['why']}" for k, v in judged.items() if not v["pass"]]
        return {"case": case, "turn": index, "persona": persona, "kind": kind, "reply": reply,
                "judge_fail": fails, "regex_fail": regex_checks(reply, checks)}

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(run, jobs))
    gold = [r for r in results if r["kind"] == "gold"]
    bad = [r for r in results if r["kind"] == "bad"]
    print(f"gold 통과율(judge): {sum(not r['judge_fail'] for r in gold)}/{len(gold)}")
    print(f"gold 통과율(regex): {sum(not r['regex_fail'] for r in gold)}/{len(gold)}")
    print(f"bad 검출(judge fail recall): {sum(bool(r['judge_fail']) for r in bad)}/{len(bad)}")
    print(f"bad 검출(judge 또는 regex): {sum(bool(r['judge_fail'] or r['regex_fail']) for r in bad)}/{len(bad)}")
    for r in gold:
        if r["judge_fail"] or r["regex_fail"]:
            print("GOLD FAIL", r["case"], r["turn"], r["persona"], r["reply"], r["judge_fail"], r["regex_fail"])
    for r in bad:
        if not r["judge_fail"] and not r["regex_fail"]:
            print("BAD MISSED", r["case"], r["turn"], r["persona"], r["reply"])
    (ROOT / "eval/calibration/latest.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--character", default="v4")
    parser.add_argument("--analyst", default="v4")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--dry", action="store_true", help="LLM 없이 코드 판정만 검사")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="실패 내용 없이 합계만 출력(holdout)")
    args = parser.parse_args()

    if args.calibrate:
        calibrate(args)
        return
    cases = load_cases(args.split)
    if args.dry:
        args.samples = 0
        llm = judge_llm = ScriptedLLM({"analysis": [], "drama": []})
        llm.json = lambda *a, **k: {"action_type": "short_talk", "facts": [], "events": [],
                                    "emotional": False, "relationship": False, "tension": False, "peak": False}
    else:
        llm = GeminiLLM(args.model)
        judge_llm = GeminiLLM(args.model)

    jobs = [(c, p) for c in cases for p in PERSONAS]
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda job: run_case(job[0], job[1], args, llm, judge_llm, args.character), jobs))
    rows = [r for group in results for r in group]
    summary = summarize(rows)
    if args.dry:
        for r in rows:
            for err in r["world_errors"]:
                print(f"WORLD {r['case']}#{r['turn']} {r['persona']}: {err}")
        print("world_errors:", summary["world_errors"])
        return
    summary["usage"] = {"input": llm.usage.input_tokens, "output": llm.usage.output_tokens, "calls": llm.usage.calls}
    out = ROOT / "eval/reports" / f"{args.character}_{args.analyst}_{args.split}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.quiet:
        print_failures(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
