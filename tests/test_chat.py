from chat import terminal


class FakeSession:
    name = "수아"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.heard: list[str | None] = []

    def reply(self, user_text):
        self.heard.append(user_text)
        self.calls.append({"label": "character", "input": 10, "cache_read": 90, "cache_write": 0, "output": 5, "cost_usd": 0.0})
        return "왔네."


def test_cache_hit_rate_is_read_over_all_input():
    assert terminal.hit({"input": 10, "cache_read": 60, "cache_write": 30}) == "60%"
    assert terminal.hit({"input": 0, "cache_read": 0, "cache_write": 0}) == "-"


def test_run_quits_on_korean_command_and_writes_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "ROOT", tmp_path)
    lines = iter(["안녕", "!종료"])
    monkeypatch.setattr(terminal, "read_line", lambda: next(lines))
    session = FakeSession()
    path = terminal.run(session, intro="시작")
    assert session.heard == [None, "안녕"]
    text = path.read_text(encoding="utf-8")
    assert "나: 안녕" in text and "수아: 왔네." in text and "캐시 적중 90%" in text
