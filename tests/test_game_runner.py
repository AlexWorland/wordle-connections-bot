import json

import httpx
import respx

from app.config import Settings
from app.engines.connections_engine import ConnectionsEngine
from app.engines.models import GameType, Outcome
from app.engines.wordle_engine import WordleEngine
from app.players.llm_player import InvalidMoveExhausted
from app.runner.game_runner import run_connections, run_wordle
from app.storage.db import GameRepository, init_db


def _settings(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
    return Settings()


class _WinningWordlePlayer:
    """Drives a real WordleEngine straight to a win."""

    def play_wordle(self, engine: WordleEngine):
        engine.apply_guess("crane")
        engine.apply_guess("token")
        return [], None

    def play_connections(self, engine: ConnectionsEngine):
        return []


class _ExhaustingPlayer:
    def play_wordle(self, engine: WordleEngine):
        raise InvalidMoveExhausted("wordle")

    def play_connections(self, engine: ConnectionsEngine):
        raise InvalidMoveExhausted("connections")


class _WinningConnectionsPlayer:
    def play_wordle(self, engine: WordleEngine):
        return []

    def play_connections(self, engine: ConnectionsEngine):
        engine.submit(["CAVITY", "NICHE", "NOOK", "RECESS"])
        engine.submit(["CHEEK", "GALL", "NERVE", "SPINE"])
        engine.submit(["ATLAS", "HERA", "PARIS", "TITAN"])
        engine.submit(["KINDLE", "SORTING", "TYPEFACE", "BREEDING"])
        return []


def _rec(result):
    """run_wordle/run_connections now return (record, embed, postmortem) or None."""
    return result[0] if result is not None else None


def _mock_wordle():
    body = json.load(open("tests/fixtures/wordle_2026-06-17.json"))
    respx.get("https://www.nytimes.com/svc/wordle/v2/2026-06-17.json").mock(
        return_value=httpx.Response(200, json=body)
    )


def _mock_connections():
    body = json.load(open("tests/fixtures/connections_2026-06-17.json"))
    respx.get("https://www.nytimes.com/svc/connections/v2/2026-06-17.json").mock(
        return_value=httpx.Response(200, json=body)
    )


@respx.mock
def test_run_wordle_saves_win_and_posts(monkeypatch, tmp_path):
    _mock_wordle()
    discord = respx.post("https://discord.com/api/webhooks/1/tok").mock(
        return_value=httpx.Response(204)
    )
    settings = _settings(monkeypatch)
    repo = GameRepository(init_db(str(tmp_path / "g.db")))

    rec = _rec(run_wordle("2026-06-17", settings, player=_WinningWordlePlayer(), repo=repo))

    assert rec is not None
    assert rec.game_type is GameType.WORDLE
    assert rec.outcome is Outcome.WIN
    assert rec.puzzle_date == "2026-06-17"
    assert repo.exists("wordle", "2026-06-17", settings.ollama_model)
    assert discord.call_count == 1


@respx.mock
def test_run_wordle_idempotent_skips(monkeypatch, tmp_path):
    _mock_wordle()
    discord = respx.post("https://discord.com/api/webhooks/1/tok").mock(
        return_value=httpx.Response(204)
    )
    settings = _settings(monkeypatch)
    repo = GameRepository(init_db(str(tmp_path / "g.db")))

    first = _rec(run_wordle("2026-06-17", settings, player=_WinningWordlePlayer(), repo=repo))
    assert first is not None
    assert discord.call_count == 1

    again = _rec(run_wordle("2026-06-17", settings, player=_WinningWordlePlayer(), repo=repo))
    assert again is None
    assert discord.call_count == 1  # nothing posted on the no-op re-run


@respx.mock
def test_run_wordle_force_replays(monkeypatch, tmp_path):
    _mock_wordle()
    respx.post("https://discord.com/api/webhooks/1/tok").mock(
        return_value=httpx.Response(204)
    )
    settings = _settings(monkeypatch)
    repo = GameRepository(init_db(str(tmp_path / "g.db")))

    run_wordle("2026-06-17", settings, player=_WinningWordlePlayer(), repo=repo)
    replayed = _rec(run_wordle(
        "2026-06-17", settings, player=_WinningWordlePlayer(), repo=repo, force=True
    ))

    assert replayed is not None
    assert replayed.outcome is Outcome.WIN


@respx.mock
def test_run_wordle_not_published_returns_none(monkeypatch, tmp_path):
    respx.get("https://www.nytimes.com/svc/wordle/v2/2099-01-01.json").mock(
        return_value=httpx.Response(404, json={"status": "ERROR"})
    )
    discord = respx.post("https://discord.com/api/webhooks/1/tok").mock(
        return_value=httpx.Response(204)
    )
    settings = _settings(monkeypatch)
    repo = GameRepository(init_db(str(tmp_path / "g.db")))

    rec = _rec(run_wordle("2099-01-01", settings, player=_WinningWordlePlayer(), repo=repo))

    assert rec is None
    assert discord.call_count == 0


@respx.mock
def test_run_wordle_exhausted_records_errored(monkeypatch, tmp_path):
    _mock_wordle()
    discord = respx.post("https://discord.com/api/webhooks/1/tok").mock(
        return_value=httpx.Response(204)
    )
    settings = _settings(monkeypatch)
    repo = GameRepository(init_db(str(tmp_path / "g.db")))

    rec = _rec(run_wordle("2026-06-17", settings, player=_ExhaustingPlayer(), repo=repo))

    assert rec is not None
    assert rec.outcome is Outcome.ERRORED
    assert repo.exists("wordle", "2026-06-17", settings.ollama_model)
    assert discord.call_count == 0  # post_on_fetch_failure defaults to False


@respx.mock
def test_run_connections_saves_win_and_posts(monkeypatch, tmp_path):
    _mock_connections()
    discord = respx.post("https://discord.com/api/webhooks/1/tok").mock(
        return_value=httpx.Response(204)
    )
    settings = _settings(monkeypatch)
    repo = GameRepository(init_db(str(tmp_path / "g.db")))

    rec = _rec(run_connections(
        "2026-06-17", settings, player=_WinningConnectionsPlayer(), repo=repo
    ))

    assert rec is not None
    assert rec.game_type is GameType.CONNECTIONS
    assert rec.outcome is Outcome.WIN
    assert rec.num_mistakes == 0
    assert repo.exists("connections", "2026-06-17", settings.ollama_model)
    assert discord.call_count == 1
