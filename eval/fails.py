"""리포트의 실패를 유형별로 짧게 본다. 사용: python eval/fails.py v2_v2_dev"""
import collections
import json
import sys
from pathlib import Path

report = json.loads((Path(__file__).parent / "reports" / f"{sys.argv[1]}.json").read_text(encoding="utf-8"))
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 8
counts, examples, lengths = collections.Counter(), collections.defaultdict(list), []
for r in report["rows"]:
    for e in r["analysis_errors"] + r["drama_errors"]:
        print("AN", r["case"], r["turn"], r["persona"], e)
    for s in r["samples"]:
        lengths.append(len(s["reply"]))
        for j in s["judge_fail"]:
            key = j.split(":")[0]
            key = "case" if key.startswith("case") else key
            counts[key] += 1
            examples[key].append((r["case"], r["turn"], r["persona"], j[:90], s["reply"][:150].replace("\n", " ")))
        for x in s["regex"]:
            counts["regex"] += 1
            examples["regex"].append((r["case"], r["turn"], r["persona"], x[:50], s["reply"][:150].replace("\n", " ")))
print(dict(counts), "평균 길이", round(sum(lengths) / max(len(lengths), 1)), "최대", max(lengths, default=0))
for key, rows in examples.items():
    print("==", key)
    for row in rows[:limit]:
        print(row)
print(json.dumps(report["summary"], ensure_ascii=False))
