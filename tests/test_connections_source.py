import json

import httpx
import pytest
import respx
from app.config import Settings
from app.engines.models import Level
from app.puzzles.connections_source import fetch_connections
from app.puzzles.wordle_source import PuzzleNotPublished

def _settings(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    return Settings()

@respx.mock
def test_levels_derived_from_order(monkeypatch):
    body = json.load(open("tests/fixtures/connections_2026-06-17.json"))
    respx.get("https://www.nytimes.com/svc/connections/v2/2026-06-17.json").mock(return_value=httpx.Response(200, json=body))
    p = fetch_connections("2026-06-17", _settings(monkeypatch))
    assert len(p.groups) == 4
    assert p.groups[0].level is Level.YELLOW and p.groups[0].title == "ALCOVE"
    assert p.groups[3].level is Level.PURPLE
    assert all(len(g.words) == 4 for g in p.groups)

@respx.mock
def test_404_raises(monkeypatch):
    respx.get("https://www.nytimes.com/svc/connections/v2/2099-01-01.json").mock(return_value=httpx.Response(404, json={"status":"ERROR"}))
    with pytest.raises(PuzzleNotPublished):
        fetch_connections("2099-01-01", _settings(monkeypatch))
