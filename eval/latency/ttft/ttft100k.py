"""입력 약 100k에서 캐시 적중/미적중 지연을 비교한다(ttft.py의 확장).

13.5k에서는 캐시가 맞아도 응답 시작이 같았다. 프리필이 작아 고정 지연에 묻힌 것인지,
캐시가 속도에 반영되지 않는 것인지 가르려고, 미적중일 때 프리필이 보일 만큼 입력을 키운다.
입력은 실제 DM 문서(여러 판·턴의 dm_doc)를 이어 붙이고, 출력은 짧게 받는다.
- big-bust: system 맨 앞에 난수(캐시 미적중)
- big-warm: 같은 요청을 다시(캐시 적중)
- tiny: 입력 21토큰
"""
import json, sys, time, uuid, glob
sys.path.insert(0, "/Users/hwansu/today/261031/bunkerkids/src")
from park.llm import LLM

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
OUT = sys.argv[2] if len(sys.argv) > 2 else "rounds6_100k.jsonl"
TARGET_CHARS = int(sys.argv[3]) if len(sys.argv) > 3 else 0

llm = LLM()
client, model = llm.client, llm.model
docs = []
for f in sorted(glob.glob("/Users/hwansu/today/261031/bunkerkids/runs/park/1103_21251*_overtime_overtime_fix15-*.jsonl")):
    for line in open(f):
        d = json.loads(line).get("trace", {}).get("dm_doc")
        if d: docs.append(d)
big = ""
for i, d in enumerate(docs):
    if TARGET_CHARS and len(big) >= TARGET_CHARS: break
    big += f"\n<doc n={i}>\n{d}\n</doc>\n"
SYS = "아래 문서들은 측정용이다. 내용은 무시하고 '네' 한 글자만 답한다."


def call(kind, sys_text, user_text, key):
    t0 = time.perf_counter(); marks = {}; usage = None
    for ev in client.responses.create(model=model, input=[{"role": "system", "content": sys_text}, {"role": "user", "content": user_text}],
                                      reasoning={"effort": "low"}, max_output_tokens=64, prompt_cache_key=key, stream=True):
        t = time.perf_counter() - t0
        if ev.type == "response.created": marks.setdefault("created", t)
        elif ev.type == "response.output_item.added": marks.setdefault("first_item", t)
        elif ev.type == "response.output_text.delta": marks.setdefault("first_text", t)
        elif ev.type in ("response.completed", "response.incomplete"):
            marks["done"] = t; usage = ev.response.usage
    d = usage.output_tokens_details
    row = dict(kind=kind, **{k: round(v, 3) for k, v in marks.items()}, input=usage.input_tokens,
               cached=usage.input_tokens_details.cached_tokens or 0, output=usage.output_tokens,
               reasoning=getattr(d, "reasoning_tokens", None))
    print(json.dumps(row, ensure_ascii=False), flush=True)
    with open(OUT, "a") as f: f.write(json.dumps(row) + "\n")
    return row


call("warmup", SYS, big, "ttft-big")
for i in range(ROUNDS):
    call("big-bust", f"[{uuid.uuid4().hex}]\n" + SYS, big, "ttft-big")
    call("big-warm", SYS, big, "ttft-big")
    call("tiny", "한 글자로 답한다.", "네라고 답해.", "ttft-tiny")
