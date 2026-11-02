"""실제로 돈 대화 기록을 이벤트 로그만 읽어 검사한다(teacher forcing 없음).

사용: python eval/live_checks.py runs/*.jsonl
"""
import json
import re
import sys
from pathlib import Path

COAT = r"겉옷|외투|재킷|자켓|코트|점퍼|겨울옷"
COLD_FORESHADOW = r"추우면|춥다고|추워질|감기|쌀쌀해질"


def check(path: Path) -> list[tuple[str, bool, str]]:
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    by = lambda k: [e for e in events if e["kind"] == k]
    replies = {e["turn"]: e["text"] for e in by("character_reply")}
    directives = {e["name"]: e["turn"] for e in by("directive")}
    turn_end = {e["turn"]: e["world"] for e in by("turn_end")}
    results = []

    stored = by("memory_stored")
    results.append(("아침에 옷 보온성 저장", any(e["value"] == "얇음" and e["clock"] == 600 for e in stored),
                    str([(e["value"], e["quote"]) for e in stored])))

    coat_turn = directives.get("offer_coat")
    before = [t for t in replies if coat_turn and t < coat_turn and re.search(COAT + "|" + COLD_FORESHADOW, replies[t])]
    results.append(("19:00 전에는 겉옷·추위 언급 없음", coat_turn is not None and not before,
                    "; ".join(f"T{t}: {replies[t][:50]}" for t in before)))
    if coat_turn:
        text = replies[coat_turn]
        recalled = [e for e in by("memory_retrieved") if e["turn"] == coat_turn]
        results.append(("추움 판정 턴에 기억 검색", bool(recalled), str(recalled[0]["items"][0]["text"]) if recalled else ""))
        results.append(("응답이 아침 기억을 꺼내고 겉옷을 건넴",
                        bool(re.search(r"아까|아침|낮에|얇", text) and re.search(COAT, text)), text))

    meal = directives.get("suggest_meal")
    results.append(("캐릭터가 먼저 식사 제안", meal is not None, replies.get(meal, "")))
    notice, resolve = directives.get("sauce_notice"), directives.get("sauce_resolve")
    results.append(("소스: 말 → 유저 반응 → 닦기", bool(notice and resolve == notice + 1),
                    f"{replies.get(notice, '')} / {replies.get(resolve, '')}"))
    if notice:
        results.append(("소스: 첫 턴에 닦지 않음", not re.search(r"닦아|닦았|닦으", replies[notice]), replies[notice]))

    drama = by("drama_decision")
    results.append(("drama manager 판단 기록", any(e["action"] == "fire" for e in drama),
                    " → ".join(e["reason"] for e in drama)))
    fire = directives.get("fireworks_fire")
    if fire and path.stem.endswith("confession"):
        results.append(("고백 플롯: 한 턴 이상 미룸", any(e["action"] == "defer" for e in drama), ""))
    if fire:
        results.append(("불꽃놀이 묘사", bool(re.search(r"빵|펑|불꽃|터", replies[fire])), replies[fire]))
        results.append(("불꽃놀이 한도(20:30) 준수", turn_end[fire]["clock"] <= 20 * 60 + 40, turn_end[fire]["clock_text"]))
    farewell = directives.get("ask_farewell")
    results.append(("귀가 갈림길에서 유저에게 물음", bool(farewell and "?" in replies[farewell]), replies.get(farewell, "")))
    last = by("run_end")[-1]
    results.append(("하루 완주(30~40턴 목표)", last["ended"] and 30 <= last["turn"] <= 40, f"{last['turn']}턴"))
    meta = [t for t, r in replies.items() if "유저" in r]
    results.append(("메타 단어 노출 없음", not meta, str(meta)))
    early_hunger = [t for t in replies if meal and t < meal and re.search(r"배고|출출|점심", replies[t])]
    results.append(("12:00 판정 전에 배고픔 언급 없음", meal is not None and not early_hunger, str(early_hunger)))
    summaries = [e["summary"] for e in by("episode_closed")]
    leaked = [x for x in summaries if re.search(r"옷|얇", x)]
    results.append(("에피소드 요약에 옷 정보 없음", not leaked, str(leaked)))
    long = [t for t, r in replies.items() if len(r) > 160]
    results.append(("응답 길이 160자 이내", not long, str(long)))
    scenes = [e["next_scene"] for e in by("episode_closed")]
    results.append(("장면 순서", scenes[:5] == ["놀이동산", "식당", "놀이동산", "산책", "불꽃놀이"], " → ".join(scenes)))
    return results


def main() -> None:
    total = passed = 0
    for name in sys.argv[1:]:
        print(f"\n# {name}")
        for label, ok, detail in check(Path(name)):
            total += 1
            passed += ok
            print(f"{'PASS' if ok else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))
    print(f"\n합계 {passed}/{total}")


if __name__ == "__main__":
    main()
