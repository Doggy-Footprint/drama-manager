"""지시 없이 상태만 줬을 때의 비교. compare.py의 있음/없음 결과에 세 번째 조건을 더한다.

입력은 같은 턴의 있음 조건에서 '지금 할 일'과 그 규칙만 뺀 것이다. 판정과 검색된 기억은 그대로 둔다.
기존 조건은 다시 생성하지 않는다.

사용: python eval/compare_state.py [--samples 3] [--dry]
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

from compare import CHECKS, JUDGE, JUDGE_SCHEMA, ROOT, RUNS, clean, replay
from worldchat.engine import IDLE_TEXT, load_yaml
from worldchat.llm import DEFAULT_MODEL, GeminiLLM

OUT = ROOT / "eval/reports/compare_state_v1.json"
STATE = load_yaml("character", "state_only_v1.yaml")
WORLD_RULES = load_yaml("character", "v4.yaml")["rules"].strip()
OLD_HEADER = load_yaml("character", "v4.yaml")["situation_header"].strip()


def state_prompt(turn: dict) -> tuple[str, list[dict]]:
    """있음 조건의 프롬프트에서 지시만 사실로 바꾼다."""
    system, messages = turn["world_prompt"]
    if WORLD_RULES not in system:
        raise AssertionError("v4 규칙을 찾지 못함")
    situation = turn["situation"]
    kept = [line for line in situation.split("\n")
            if line != OLD_HEADER and not line.startswith(("판정: ", "지금 할 일: "))]
    fact = STATE["facts"].get(turn["directive"])
    new = [STATE["situation_header"].strip(), kept[0]] + ([f"상태: {fact}"] if fact else []) + kept[1:]
    last = messages[-1]
    if not last["text"].endswith(situation):
        raise AssertionError("마지막 메시지 끝에 현재 상황이 없음")
    said = last["text"][: -len(situation)]
    return system.replace(WORLD_RULES, STATE["rules"].strip()), messages[:-1] + [{"role": "user", "text": said + "\n".join(new)}]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--dry", action="store_true", help="LLM 없이 프롬프트만 출력한다")
    args = parser.parse_args()

    points = {name: [t for t in replay(name) if t["directive"] in CHECKS] for name in RUNS}
    prompts = {(name, t["turn"]): state_prompt(t) for name, ts in points.items() for t in ts}
    if args.dry:
        for t in points[RUNS[0]]:
            print(f"--- T{t['turn']} {t['directive']}\n{prompts[(RUNS[0], t['turn'])][1][-1]['text']}\n")
        return

    llm = GeminiLLM(DEFAULT_MODEL)
    pool = ThreadPoolExecutor(8)

    def gen(prompt) -> str:
        return clean(llm.text(prompt[0], prompt[1]))

    def judge(user, reply, question) -> bool:
        text = f"상대의 말: {user if user is not None else IDLE_TEXT}\n캐릭터 응답: {reply}\n질문: {question}"
        return bool(llm.json(JUDGE["system"], [{"role": "user", "text": text}], JUDGE_SCHEMA)["answer"])

    replies = {(name, t["turn"], i): pool.submit(gen, prompts[(name, t["turn"])])
               for name, ts in points.items() for t in ts for i in range(args.samples)}
    replies = {k: f.result() for k, f in replies.items()}
    verdicts = {(name, t["turn"], q, i): pool.submit(judge, t["user"], replies[(name, t["turn"], i)], check[1])
                for name, ts in points.items() for t in ts
                for q, check in enumerate(CHECKS[t["directive"]]) for i in range(args.samples)}
    verdicts = {k: f.result() for k, f in verdicts.items()}

    report = {"meta": {"model": DEFAULT_MODEL, "prompt": "state_only_v1", "judge": "compare_judge_v1", "samples": args.samples,
                       "usage": {"input": llm.usage.input_tokens, "output": llm.usage.output_tokens, "calls": llm.usage.calls}},
              "runs": {}}
    for name, ts in points.items():
        rows = []
        for t in ts:
            for q, (label, _) in enumerate(CHECKS[t["directive"]]):
                rows.append({"turn": t["turn"], "check": t["directive"], "label": label,
                             "samples": [{"text": replies[(name, t["turn"], i)], "yes": verdicts[(name, t["turn"], q, i)]}
                                         for i in range(args.samples)]})
        report["runs"][name] = rows
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    for name, rows in report["runs"].items():
        print(f"\n# {name}")
        for r in rows:
            print(f"T{r['turn']} {r['label']} {sum(x['yes'] for x in r['samples'])}/{args.samples}")
            for x in r["samples"]:
                print(f"   {'Y' if x['yes'] else 'N'} {x['text'][:120]}")
    print("\n", report["meta"]["usage"])


if __name__ == "__main__":
    main()
