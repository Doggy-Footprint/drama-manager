"""시연용 웹 서버(표준 라이브러리만 사용). 세션 하나를 들고, 턴의 단계를 NDJSON으로 흘려보낸다.

  cd /Users/hwansu/today/261031/bunkerkids
  PYTHONPATH=src .venv/bin/python -m park            # http://127.0.0.1:8765
  PYTHONPATH=src .venv/bin/python -m park --port 9000

API
  GET  /api/state                     지금 상태 전체
  GET  /api/trace?n=3                 n번째 턴의 판단 기록 전체(캐릭터/DM에 들어간 XML 포함)
  GET  /api/runs                      저장된 로그(runs/park/*.jsonl) 목록
  GET  /api/run?file=...              저장된 로그 하나를 화면이 그릴 수 있는 형태로(읽기 전용)
  POST /api/reset {"baseline": true}  새 세션
  POST /api/turn  {"text": "..."}     한 턴. text가 비면 시작 턴(수아가 먼저 말한다). 응답은 NDJSON 스트림
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .llm import ROOT
from .session import Session

WEB = Path(__file__).resolve().parent / "web"
RUNS = ROOT / "runs" / "park"


def list_runs() -> list[dict]:
    out = []
    for f in sorted(RUNS.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        lines = f.read_text(encoding="utf-8").splitlines()
        last = json.loads(lines[-1]) if lines else {}
        out.append({"file": f.name, "turns": len(lines), "clock": last.get("trace", {}).get("clock_after"),
                    "viewable": "state" in last})
    return out


def load_run(name: str) -> dict:
    """로그 한 줄은 {entry, trace, state}다. 마지막 줄의 state(그 턴이 끝난 뒤 상태)와 모든 trace를 돌려준다."""
    if not re.fullmatch(r"[\w\-]+\.jsonl", name) or not (RUNS / name).exists():
        return {"error": "없는 로그"}
    rows = [json.loads(x) for x in (RUNS / name).read_text(encoding="utf-8").splitlines() if x.strip()]
    traces = {r["trace"]["n"]: r["trace"] for r in rows}
    state = rows[-1].get("state") if rows else None
    if not state:  # state를 남기기 전의 옛 로그. 대화와 긴장 곡선만 보인다
        state = {"n": len(rows), "log": [r["entry"] for r in rows],
                 "tension": [{"n": r["trace"]["n"], "beat": r["trace"]["dm"]["beat"], "tension": r["trace"]["dm"]["tension"]}
                             for r in rows if "dm" in r["trace"]]}
    return {"file": name, "state": state, "traces": traces}


class App:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.session: Session | None = None

    def reset(self, baseline: bool) -> Session:
        self.session = Session(baseline=baseline)
        return self.session


APP = App()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 조용히
        pass

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            body = (WEB / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/state":
            s = APP.session
            self._json(s.snapshot() if s else {"n": 0, "empty": True})
        elif url.path == "/api/runs":
            self._json(list_runs())
        elif url.path == "/api/run":
            self._json(load_run(parse_qs(url.query).get("file", [""])[0]))
        elif url.path == "/api/trace":
            n = int(parse_qs(url.query).get("n", ["0"])[0])
            s = APP.session
            self._json(s.traces.get(n, {}) if s else {})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/reset":
            body = self._body()
            with APP.lock:
                s = APP.reset(bool(body.get("baseline")))
            self._json(s.snapshot())
        elif url.path == "/api/turn":
            body = self._body()
            text = (body.get("text") or "").strip() or None
            if APP.session is None:
                APP.reset(False)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(event: dict) -> None:
                self.wfile.write((json.dumps(event, ensure_ascii=False, default=str) + "\n").encode())
                self.wfile.flush()

            if not APP.lock.acquire(blocking=False):
                emit({"type": "error", "text": "이전 턴을 처리하는 중이다"})
                return
            try:
                APP.session.turn(text, emit)
            except Exception as e:  # 시연 중 끊기지 않게 화면에 보여 준다
                traceback.print_exc()
                emit({"type": "error", "text": f"{type(e).__name__}: {e}"})
            finally:
                APP.lock.release()
        else:
            self._json({"error": "not found"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
