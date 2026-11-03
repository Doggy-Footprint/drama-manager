"""TTFT가 프리필 탓인지 확인한다.

같은 DM 요청(실제 trace의 dm_doc, 입력 약 13.5k)을
- warm: 같은 앞부분 그대로 다시 보낸다(캐시 적중 기대)
- bust: system 맨 앞에 난수를 붙여 캐시를 깬다(길이는 같다)
로 번갈아 보내고, 스트리밍 이벤트 시각을 잰다.
같은 큰 입력에 출력만 몇 토큰인 요청(prefill-*)과, 입력이 아주 작은 요청(tiny)도 같이 잰다.
"""
import json, os, sys, time, uuid, statistics as st
sys.path.insert(0, "/Users/hwansu/today/261031/bunkerkids/src")
from park.llm import LLM
from park.session import Session, dm_schema, DM_EFFORT

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
OUT = sys.argv[2] if len(sys.argv) > 2 else "ttft.jsonl"

llm = LLM()
client, model = llm.client, llm.model
s = Session(scenario="overtime", llm=llm)
rows = [json.loads(l) for l in open("/Users/hwansu/today/261031/bunkerkids/runs/park/1103_212511_overtime_overtime_fix15-b.jsonl")]
doc = rows[5]["trace"]["dm_doc"]          # T6, 기록상 DM 입력 13,490 / 출력 605 / 8.87초
system = s.prompt("dm")
c = s.character.data
schema = dm_schema(name=s.name, locations=list(s.world.locations), event_ids=s._all_event_ids(),
                   detail_ids=s._all_detail_ids(), secret_ids=[x["id"] for x in c["secrets"]],
                   grievance_ids=[g["id"] for g in c["dynamic"]["grievances"]], goal_ids=[g["id"] for g in c["goals"]],
                   item_ids=list(s.world.items), relation=s.relation)
fmt = {"format": {"type": "json_schema", "name": "drama_manager", "strict": True, "schema": schema}}


def call(kind, sys_text, user_text, *, text=fmt, effort=DM_EFFORT, max_out=None, key="ttft-dm"):
    t0 = time.perf_counter()
    marks = {}
    kw = dict(model=model, input=[{"role": "system", "content": sys_text}, {"role": "user", "content": user_text}],
              reasoning={"effort": effort}, prompt_cache_key=key, stream=True)
    if text: kw["text"] = text
    if max_out: kw["max_output_tokens"] = max_out
    usage = None
    for ev in client.responses.create(**kw):
        t = time.perf_counter() - t0
        typ = ev.type
        if typ == "response.created": marks.setdefault("created", t)
        elif typ == "response.output_item.added":
            marks.setdefault("first_item", t)
            if getattr(ev.item, "type", "") == "message": marks.setdefault("msg_item", t)
        elif typ == "response.output_text.delta": marks.setdefault("first_text", t)
        elif typ in ("response.completed", "response.incomplete"):
            marks["done"] = t
            usage = ev.response.usage
    d = usage.output_tokens_details if usage else None
    row = dict(kind=kind, **{k: round(v, 3) for k, v in marks.items()},
               input=usage.input_tokens, cached=usage.input_tokens_details.cached_tokens or 0,
               output=usage.output_tokens, reasoning=getattr(d, "reasoning_tokens", None) if d else None)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    with open(OUT, "a") as f: f.write(json.dumps(row) + "\n")
    return row


# 준비: 스키마 컴파일과 warm 캐시를 채운다(집계에서 뺀다)
call("warmup", system, doc)
PREFILL_SYS = system + "\n\n(측정용) 위 지시는 무시하고 '네' 한 글자만 답한다."
call("warmup", PREFILL_SYS, doc, text=None, effort="low", max_out=64, key="ttft-prefill")

for i in range(ROUNDS):
    nonce = uuid.uuid4().hex
    call("dm-bust", f"[{nonce}]\n" + system, doc)
    call("dm-warm", system, doc)
    call("prefill-bust", f"[{nonce}]\n" + PREFILL_SYS, doc, text=None, effort="low", max_out=64, key="ttft-prefill")
    call("prefill-warm", PREFILL_SYS, doc, text=None, effort="low", max_out=64, key="ttft-prefill")
    call("tiny", "한 글자로 답한다.", "네라고 답해.", text=None, effort="low", max_out=64, key="ttft-tiny")
