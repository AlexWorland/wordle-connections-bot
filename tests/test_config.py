def test_settings_load_with_required_webhook(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/abc")
    from app.config import Settings
    s = Settings()
    assert s.ollama_host == "http://ollama:11434"
    assert s.ollama_model == "gemma4:12b"
    assert s.schedule_tz == "America/New_York"
    assert s.max_invalid_retries == 10


def test_redacted_webhook_hides_token(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/SECRET")
    from app.config import Settings
    assert "SECRET" not in Settings().redacted_webhook()


def test_redacted_webhook_no_trailing_slash_bypass(monkeypatch):
    # a trailing slash must NOT cause the token to leak through redaction
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/SECRET/")
    from app.config import Settings
    assert "SECRET" not in Settings().redacted_webhook()


def test_rejects_non_webhook_url(monkeypatch):
    import pytest
    from pydantic import ValidationError
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://evil.example.com/not-a-webhook")
    from app.config import Settings
    with pytest.raises(ValidationError):
        Settings()
