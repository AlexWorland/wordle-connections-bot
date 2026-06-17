import json

import pytest

from app.config import Settings
from app.engines.models import WordlePuzzle
from app.engines.wordle_engine import WordleEngine
from app.players.llm_player import LLMPlayer, InvalidMoveExhausted


class FakeOllama:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def chat(self, **kw):
        r = self._replies[self.calls]
        self.calls += 1
        return type("M", (), {"message": type("Msg", (), {"content": r})})


def _settings(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    return Settings()


def test_invalid_then_valid_does_not_consume_turn(monkeypatch):
    eng = WordleEngine(WordlePuzzle("2026-06-17", 1824, "token", None), allowed={"crane", "token"})
    fake = FakeOllama([
        json.dumps({"reasoning": "try", "guess": "zzzzz"}),   # not in dictionary -> re-prompt, no turn
        json.dumps({"reasoning": "ok", "guess": "crane"}),    # valid -> consumes turn 1
        json.dumps({"reasoning": "win", "guess": "token"}),   # valid -> win
    ])
    turns = LLMPlayer(_settings(monkeypatch), client=fake).play_wordle(eng)
    assert eng.solved
    assert [t.guess for t in turns] == ["crane", "token"]   # invalid attempt NOT recorded as a turn
    assert turns[0].retries == 1                            # one corrective re-prompt before crane


def test_backstop_raises(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    s = Settings(max_invalid_retries=2)
    eng = WordleEngine(WordlePuzzle("2026-06-17", 1824, "token", None), allowed={"crane"})
    fake = FakeOllama([json.dumps({"reasoning": "x", "guess": "zzzzz"})] * 5)
    with pytest.raises(InvalidMoveExhausted):
        LLMPlayer(s, client=fake).play_wordle(eng)


def test_strip_code_fence():
    from app.players.llm_player import _strip_code_fence
    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('```\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('{"a":1}') == '{"a":1}'
    assert _strip_code_fence('  {"a":1}  ') == '{"a":1}'
