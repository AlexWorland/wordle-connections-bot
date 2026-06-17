import json

import httpx
import pytest
import respx

from app.config import Settings
from app.puzzles.wordle_source import fetch_wordle, PuzzleNotPublished

def _settings(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    return Settings()

@respx.mock
def test_parses_modern_record(monkeypatch):
    body = json.load(open("tests/fixtures/wordle_2026-06-17.json"))
    respx.get("https://www.nytimes.com/svc/wordle/v2/2026-06-17.json").mock(return_value=httpx.Response(200, json=body))
    p = fetch_wordle("2026-06-17", _settings(monkeypatch))
    assert p.solution == "token" and p.number == 1824 and p.editor == "Tracy Bennett"

@respx.mock
def test_parses_old_record_without_optional_fields(monkeypatch):
    body = json.load(open("tests/fixtures/wordle_launch.json"))
    respx.get("https://www.nytimes.com/svc/wordle/v2/2021-06-19.json").mock(return_value=httpx.Response(200, json=body))
    p = fetch_wordle("2021-06-19", _settings(monkeypatch))
    assert p.solution == "cigar" and p.number is None and p.editor is None

@respx.mock
def test_404_raises_not_published(monkeypatch):
    respx.get("https://www.nytimes.com/svc/wordle/v2/2099-01-01.json").mock(return_value=httpx.Response(404, json={"status":"ERROR"}))
    with pytest.raises(PuzzleNotPublished):
        fetch_wordle("2099-01-01", _settings(monkeypatch))
