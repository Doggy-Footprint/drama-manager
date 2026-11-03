"""터미널에서 세션을 진행한다. 웹 화면과 같은 로그(runs/park/<시각>_<이름>.jsonl)를 남기므로, 웹의 "저장된 로그"에서 그대로 볼 수 있다.

  PYTHONPATH=src .venv/bin/python -m park.cli new 이름 [--baseline]   새 세션을 열고 시작 턴을 진행한다
  PYTHONPATH=src .venv/bin/python -m park.cli say 이름 "말 (행동)"     한 턴
  PYTHONPATH=src .venv/bin/python -m park.cli log 이름                 지금까지의 대화

기본 출력은 유저가 화면에서 보는 것(시각, 위치, 지문, 수아의 말)뿐이다. --verbose를 붙이면 드라마 매니저의 판단과 코드 검증도 찍는다.
세션은 턴 사이에 runs/park/sessions/<이름>.state.pkl로 저장된다.
"""
from __future__ import annotations

import argparse
import pickle
import re
import sys

from .llm import ROOT
from .session import Session

STORE = ROOT / "runs" / "park" / "sessions"


def path(name: str):
    if not re.fullmatch(r"[\w\-]+", name):
        sys.exit("이름은 영문, 숫자, _, - 만 쓴다")
    return STORE / f"{name}.state.pkl"


def save(name: str, s: Session) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    path(name).write_bytes(pickle.dumps(s))


def load(name: str) -> Session:
    p = path(name)
    if not p.exists():
        sys.exit(f"세션 {name}이 없다. new로 먼저 연다")
    return pickle.loads(p.read_bytes())


def where(s: Session) -> str:
    w = s.world
    if w.together:
        return f"{w.loc_name(w.user_loc)} · 수아와 함께"
    return f"{w.loc_name(w.user_loc)} · 수아와 떨어져 있음"


def show(s: Session, entry: dict, verbose: bool) -> None:
    t = s.traces[entry["n"]]
    print(f"[T{entry['n']} {t['clock_before']}→{t['clock_after']} · {where(s)}]")
    if verbose:
        d = t["dm"]
        print(f"  DM {d['beat']} 긴장{d['tension']}: {d['scene_read']}")
        print(f"  속마음: {d['directive']}")
        for note in t["applied"]["notes"] + t.get("character_applied", []):
            print(f"  {'' if note['ok'] else '✗ '}{note['text']}")
    if entry.get("narration"):
        print(f"※ {entry['narration']}")
    if entry.get("reply"):
        print(f"수아{'(전화)' if entry['channel'] == 'phone' else ''}: {entry['reply']}")
    else:
        print("(수아에게 닿지 않았다)")
    if entry.get("baseline"):
        print(f"기준선: {entry['baseline']}")
    if t.get("episode", {}).get("closed") and verbose:
        print(f"  {t['episode']['text']}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="park.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("new"); a.add_argument("name"); a.add_argument("--baseline", action="store_true"); a.add_argument("--verbose", action="store_true")
    b = sub.add_parser("say"); b.add_argument("name"); b.add_argument("text"); b.add_argument("--verbose", action="store_true")
    c = sub.add_parser("log"); c.add_argument("name")
    args = ap.parse_args()

    if args.cmd == "new":
        s = Session(baseline=args.baseline, tag=args.name)
        print(f"로그: {s.record.relative_to(ROOT)}")
        entry = s.turn(None)
        save(args.name, s)
        show(s, entry, args.verbose)
    elif args.cmd == "say":
        if not args.text.strip():
            sys.exit("빈 입력은 보낼 수 없다")
        s = load(args.name)
        if s.world.clock >= s.world.data["end"]:
            sys.exit(f"이야기가 끝났다({s.world.data['end']})")
        entry = s.turn(args.text.strip() or None)
        save(args.name, s)
        show(s, entry, args.verbose)
    else:
        s = load(args.name)
        print(f"로그: {s.record.relative_to(ROOT)}")
        for e in s.log:
            print(f"[T{e['n']} {e['clock']}] 나: {e['user']}")
            if e.get("narration"):
                print(f"  ※ {e['narration']}")
            print(f"  수아: {e['reply'] or '(닿지 않음)'}")


if __name__ == "__main__":
    main()
