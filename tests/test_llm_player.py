import json

import pytest

from app.config import Settings
from app.engines.models import WordlePuzzle
from app.engines.wordle_engine import WordleEngine
from app.players.backend import LLMBackend
from app.players.llm_player import InvalidMoveExhausted
from app.players.llm_player import LLMPlayer


class FakeBackend(LLMBackend):
    """Test double for LLMBackend — returns canned strings from a queue."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "fake"

    def complete(
        self,
        messages: list[dict],
        format: str | dict | None = "json",
        temperature: float | None = None,
    ) -> str:
        r = self._replies[self.calls]
        self.calls += 1
        return r


def _settings(monkeypatch) -> Settings:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    return Settings(_env_file=None)


def test_invalid_then_valid_does_not_consume_turn(monkeypatch):
    eng = WordleEngine(WordlePuzzle("2026-06-17", 1824, "token", None), allowed={"crane", "token"})
    backend = FakeBackend([
        json.dumps({"reasoning": "try", "guess": "zzzzz"}),   # not in dict → re-prompt, no turn
        json.dumps({"reasoning": "ok", "guess": "crane"}),    # valid → turn 1
        json.dumps({"reasoning": "win", "guess": "token"}),   # valid → win
    ])
    turns, _postmortem = LLMPlayer(_settings(monkeypatch), backend=backend).play_wordle(eng)
    assert eng.solved
    assert [t.guess for t in turns] == ["crane", "token"]   # invalid not recorded
    # retries now tracks schema-parse retries only; a non-word is a Phase-2 move
    # correction, so schema_retries stays 0.
    assert turns[0].retries == 0


def test_backstop_raises(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    s = Settings(_env_file=None, max_invalid_retries=2)
    eng = WordleEngine(WordlePuzzle("2026-06-17", 1824, "token", None), allowed={"crane"})
    backend = FakeBackend([json.dumps({"reasoning": "x", "guess": "zzzzz"})] * 5)
    with pytest.raises(InvalidMoveExhausted):
        LLMPlayer(s, backend=backend).play_wordle(eng)


def test_strip_code_fence():
    from app.players.llm_player import _strip_code_fence
    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('```\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('{"a":1}') == '{"a":1}'
    assert _strip_code_fence('  {"a":1}  ') == '{"a":1}'


def test_sanitize_json_strings():
    from app.players.llm_player import _sanitize_json_strings
    raw = '{"reasoning": "line1\nline2\nline3", "guess": "crane"}'
    clean = _sanitize_json_strings(raw)
    parsed = json.loads(clean)
    assert parsed["reasoning"] == "line1\nline2\nline3"
    assert parsed["guess"] == "crane"
    raw2 = '{\n  "guess": "token"\n}'
    assert json.loads(_sanitize_json_strings(raw2))["guess"] == "token"
