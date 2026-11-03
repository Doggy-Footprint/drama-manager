"""기준선 단독 대화. 엔진 없이 캐릭터 프롬프트(+지난 만남·장기 기억을 글로 붙인 것) 하나와 멀티턴으로 대화한다.

  PYTHONPATH=src .venv/bin/python <이 파일> new 이름 [--scenario overtime] [--no-memory]
  PYTHONPATH=src .venv/bin/python <이 파일> say 이름 "말 (행동)"

로그: runs/park/baseline/<시각>_<시나리오>_<이름>.jsonl (화면의 로그 목록과 섞이지 않게 하위 폴더에 둔다)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime

from park.llm import LLM, ROOT
from park.session import Session

STORE = ROOT / "runs" / "park" / "baseline"


def memory_text(s: Session) -> str:
    """흔한 방식: 기억을 요약 글로 시스템 프롬프트에 다 넣는다."""
    lines = ["", "## 지금까지의 기억"]
    lines += [f"- {m}" for m in s.character.data.get("long_term_memory", [])]
    for ep in [e for e in s.memory.episodes if e.get("prior")]:
        lines.append(f"\n### 지난 만남: {ep['title']}")
        lines.append(ep["summary"].strip())
    from park.state import load_history
    h = load_history(s.scenario) or {}
    for ep in h.get("episodes", []):
        for d in ep.get("details", []):
            lines.append(f"- ({ep['title']}) {d['text']}")
    rel = h.get("relationship")
    if rel:
        lines.append("\n## 지금 관계")
        stage = next(x["name"] for x in rel["stages"] if x["id"] == rel["stage"])
        lines.append(f"- 단계: {stage}. 호칭: 상대를 '{rel['address']['character']}'라고 부른다.")
        lines += [f"- 약속({p['status']}): {p['text']}" for p in rel.get("promises", [])]
    return "\n".join(lines)


def state_path(name: str):
    if not re.fullmatch(r"[\w\-]+", name):
        sys.exit("이름은 영문, 숫자, _, - 만 쓴다")
    return STORE / "sessions" / f"{name}.json"


def turn(st: dict, user: str) -> str:
    llm = LLM()
    st["messages"].append({"role": "user", "content": user})
    reply = llm.text(st["system"], st["messages"], label="baseline")
    st["messages"].append({"role": "assistant", "content": reply})
    n = len(st["messages"]) // 2
    with open(ROOT / st["record"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"n": n, "user": user, "reply": reply, "usage": llm.calls[-1]}, ensure_ascii=False) + "\n")
    return reply


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("new"); a.add_argument("name"); a.add_argument("--scenario", default="overtime")
    a.add_argument("--no-memory", action="store_true")
    b = sub.add_parser("say"); b.add_argument("name"); b.add_argument("text")
    args = ap.parse_args()
    p = state_path(args.name)
    if args.cmd == "new":
        s = Session(scenario=args.scenario, tag=args.name)
        system = s.prompt("baseline") + ("" if args.no_memory else memory_text(s))
        stamp = datetime.now().strftime("%m%d_%H%M%S")
        rec = (STORE / f"{stamp}_{args.scenario}_{args.name}.jsonl").relative_to(ROOT)
        p.parent.mkdir(parents=True, exist_ok=True)
        (ROOT / rec).write_text(json.dumps({"system": system}, ensure_ascii=False) + "\n", encoding="utf-8")
        st = {"system": system, "messages": [], "record": str(rec)}
        opening = s.world.scenario["opening"]
        print(f"[유저] {opening}")
        print(f"[{s.name}] {turn(st, opening)}")
        print(f"(로그: {rec})")
    else:
        if not p.exists():
            sys.exit("new로 먼저 연다")
        st = json.loads(p.read_text(encoding="utf-8"))
        print(turn(st, args.text))
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
