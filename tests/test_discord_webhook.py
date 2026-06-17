import httpx
import respx
from app.config import Settings
from app.output.discord_webhook import build_wordle_embed, post_embed

def _s(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
    return Settings()

def test_embed_spoilers_answer_and_decimal_color(monkeypatch):
    e = build_wordle_embed(1824, ["🟩🟩🟩🟩🟩"], "token", "gemma4:12b", won=True)
    assert isinstance(e["color"], int)
    assert "||" in e["description"] and "TOKEN" in e["description"].upper()
    assert "```" not in e["description"]   # spoiler must not be in a code block

@respx.mock
def test_post_sets_no_mention_and_posts(monkeypatch):
    route = respx.post("https://discord.com/api/webhooks/1/tok").mock(return_value=httpx.Response(204))
    post_embed({"title": "x"}, _s(monkeypatch))
    body = route.calls[0].request.content
    assert b'"parse": []' in body or b'"parse":[]' in body
