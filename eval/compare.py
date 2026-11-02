"""월드 모델 있음/없음 비교. 실제 실행 기록의 유저 턴을 그대로 재생한다.

두 조건이 받는 입력은 턴마다 같다: 지금까지의 대화(월드 모델 쪽 기록으로 고정)와 이번 유저 말.
- 있음: 엔진을 기록대로 재생해 얻은 프롬프트(페르소나 + 장기 기억 + 현재 상황)
- 없음: 페르소나 + 대화 기록 전체. 시각, 판정, 지시, 검색된 기억이 없다

표시용 응답은 있음 쪽은 기록 그대로, 없음 쪽은 첫 샘플이다(골라내지 않는다).
비교 지점은 있음·없음 모두 새로 N번 생성해 Judge가 행동 여부를 판정한다.

사용: python eval/compare.py [--samples 5] [--dry]
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

from worldchat.drama import DramaJudgement  # noqa: E402
from worldchat.engine import IDLE_TEXT, SCENE_START_TEXT, Engine, load_yaml  # noqa: E402
from worldchat.llm import DEFAULT_MODEL, GeminiLLM  # noqa: E402

RUNS = ["tsundere_confession", "warm_confession", "warm_ordinary"]
OUT = ROOT / "eval/reports/compare_v1.json"
JUDGE = yaml.safe_load((ROOT / "eval/prompts/compare_judge_v1.yaml").read_text(encoding="utf-8"))
JUDGE_SCHEMA = {"type": "object", "properties": {"answer": {"type": "boolean"}}, "required": ["answer"]}

# 비교 지점: 월드 모델의 판정이 행동을 시키거나 막는 턴. 지시 이름이 곧 그 턴을 가리킨다.
CHECKS = {
    "open_greeting": [("옷 이야기로 말을 건다", "상대의 옷을 언급했는가?")],
    "thin_question": [("저녁 추위나 겉옷을 미리 꺼낸다", "나중에 추워질 가능성을 비치거나 겉옷·외투 이야기를 꺼냈는가?")],
    "suggest_meal": [("먼저 점심을 제안한다", "캐릭터가 먼저 점심이나 식사를 하자고 제안했는가?")],
    "sauce_notice": [("입가의 소스를 알아챈다", "상대 얼굴이나 입가에 무언가 묻은 것을 알아채거나, 가만히 있으라고 상대를 멈춰 세웠는가?")],
    "sauce_resolve": [("입가를 닦아 주거나 알려 준다", "상대 입가에 묻은 것을 닦아 주거나 어디에 묻었는지 알려 주었는가?")],
    "afternoon_passed": [("오후가 이미 지났다고 전제한다", "놀이기구를 타거나 논 일을 이미 지나간 일로 말했는가?")],
    "offer_coat": [
        ("겉옷을 건넨다", "캐릭터가 자기 겉옷이나 외투를 벗어 주거나 입으라고 건넸는가?"),
        ("아침의 '얇다'는 말을 꺼낸다", "상대가 아침에 옷을 얇게 입었다고 한 일을 언급했는가?"),
    ],
    "fireworks_fire": [("불꽃이 터진다", "지금 불꽃이 터지는 일이 응답에 나오는가?")],
    "ask_farewell": [("집에 갈지 더 놀지 묻는다", "집에 갈지 더 놀지(또는 이제 뭘 할지) 상대에게 물었는가?")],
}


class NoLLM:
    """재생 중에는 LLM을 부르지 않는다. 불리면 기록이 부족하다는 뜻이다."""

    def text(self, *a, **k):
        raise AssertionError("재생 중 LLM 호출: 기록에 없는 판정")

    json = text


def clean(reply: str) -> str:
    return re.sub(r"\s*\n+\s*", " ", reply).strip()


def replay(name: str) -> list[dict]:
    """기록을 엔진에 그대로 넣어 턴별 프롬프트를 복원한다. 현재 상황 블록이 기록과 같아야 한다."""
    events = [json.loads(l) for l in (ROOT / "runs" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    persona = next(e for e in events if e["kind"] == "run_meta")["persona"]
    by_turn: dict[int, dict] = {}
    for e in events:
        by_turn.setdefault(e["turn"], {}).setdefault(e["kind"], []).append(e)

    engine = Engine(NoLLM(), persona)
    baseline_system = "\n\n".join([engine.persona["text"].strip(), load_yaml("character", "baseline_v1.yaml")["rules"].strip()])
    transcript: list[dict] = [{"role": "user", "text": SCENE_START_TEXT}]
    turns = []
    for n in sorted(k for k in by_turn if k > 0 and "character_reply" in by_turn[k]):
        got = by_turn[n]
        user = got["turn_start"][0]["user"]
        drama = got.get("drama_decision")
        closed = got.get("episode_closed")
        ctx = engine.prepare(
            user,
            drama=DramaJudgement(**drama[0]["judgement"]) if drama else None,
            summary=closed[0]["summary"] if closed else None,
        )
        logged = got["prompt_assembled"][0]["situation"]
        if ctx.situation != logged:
            raise AssertionError(f"{name} T{n}: 복원한 현재 상황이 기록과 다름\n{ctx.situation}\n---\n{logged}")
        reply = got["character_reply"][0]["text"]
        if n > 1:
            transcript.append({"role": "user", "text": user if user is not None else IDLE_TEXT})
        directive = got["directive"][0]["name"] if "directive" in got else None
        thin = any(e["value"] == "얇음" for e in got.get("memory_stored", []))
        turns.append({
            "turn": n, "user": user, "reply": reply, "directive": "thin_question" if thin else directive,
            "world_prompt": (ctx.system, ctx.messages), "situation": ctx.situation,
            "baseline_prompt": (baseline_system, list(transcript)),
        })
        analysis = {k: got["analysis"][0].get(k) for k in ("action_type", "facts", "events")}
        engine.finish(ctx, reply, analysis)
        transcript.append({"role": "assistant", "text": reply})
    return turns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry", action="store_true", help="LLM 없이 재생만 검증한다")
    args = parser.parse_args()

    replayed = {name: replay(name) for name in RUNS}
    for name, turns in replayed.items():
        points = [f"T{t['turn']}:{t['directive']}" for t in turns if t["directive"] in CHECKS]
        print(f"{name}: {len(turns)}턴 재생 일치, 비교 지점 {len(points)}개 {points}")
    if args.dry:
        return

    llm = GeminiLLM(DEFAULT_MODEL)
    pool = ThreadPoolExecutor(args.workers)

    def gen(prompt) -> str:
        system, messages = prompt
        return clean(llm.text(system, messages))

    def judge(user: str | None, reply: str, question: str) -> bool:
        text = f"상대의 말: {user if user is not None else IDLE_TEXT}\n캐릭터 응답: {reply}\n질문: {question}"
        return bool(llm.json(JUDGE["system"], [{"role": "user", "text": text}], JUDGE_SCHEMA)["answer"])

    # 생성
    jobs = {}
    for name, turns in replayed.items():
        for t in turns:
            is_point = t["directive"] in CHECKS
            for i in range(args.samples if is_point else 1):
                jobs[(name, t["turn"], "baseline", i)] = pool.submit(gen, t["baseline_prompt"])
                if is_point:
                    jobs[(name, t["turn"], "world", i)] = pool.submit(gen, t["world_prompt"])
    generated = {k: f.result() for k, f in jobs.items()}
    print("생성", len(generated), flush=True)

    # 판정: Judge는 어느 조건인지 모른다
    verdicts = {}
    for name, turns in replayed.items():
        for t in turns:
            for q, (_, question) in enumerate(CHECKS.get(t["directive"], [])):
                verdicts[(name, t["turn"], q, "recorded", 0)] = pool.submit(judge, t["user"], t["reply"], question)
                for arm in ("baseline", "world"):
                    for i in range(args.samples):
                        reply = generated[(name, t["turn"], arm, i)]
                        verdicts[(name, t["turn"], q, arm, i)] = pool.submit(judge, t["user"], reply, question)
    verdicts = {k: f.result() for k, f in verdicts.items()}
    print("판정", len(verdicts), flush=True)

    report = {
        "meta": {"model": DEFAULT_MODEL, "world_prompt": "v4", "baseline_prompt": "baseline_v1", "judge": "compare_judge_v1",
                 "samples": args.samples,
                 "usage": {"input": llm.usage.input_tokens, "output": llm.usage.output_tokens, "calls": llm.usage.calls}},
        "runs": {},
    }
    for name, turns in replayed.items():
        points = []
        for t in turns:
            for q, (label, question) in enumerate(CHECKS.get(t["directive"], [])):
                arm = lambda a: [{"text": generated[(name, t["turn"], a, i)], "yes": verdicts[(name, t["turn"], q, a, i)]}
                                 for i in range(args.samples)]
                points.append({"turn": t["turn"], "check": t["directive"], "label": label, "question": question,
                               "user": t["user"], "recorded_reply": t["reply"],
                               "recorded_yes": verdicts[(name, t["turn"], q, "recorded", 0)],
                               "world": arm("world"), "baseline": arm("baseline")})
        report["runs"][name] = {
            "baseline_replies": {str(t["turn"]): generated[(name, t["turn"], "baseline", 0)] for t in turns},
            "points": points,
        }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    for name, data in report["runs"].items():
        print(f"\n# {name}")
        for p in data["points"]:
            w = sum(x["yes"] for x in p["world"])
            b = sum(x["yes"] for x in p["baseline"])
            print(f"T{p['turn']:>2} {p['label']:<24} 있음 {w}/{args.samples} (기록 {'Y' if p['recorded_yes'] else 'N'}) 없음 {b}/{args.samples}")
    print("\n", report["meta"]["usage"], OUT)


if __name__ == "__main__":
    main()
