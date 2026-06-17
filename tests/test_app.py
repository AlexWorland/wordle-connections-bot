import importlib

from fastapi.testclient import TestClient


def _prepare(monkeypatch):
    """Set required env + TESTING flag, then import the app module freshly.

    The module assigns ``app = build_app(get_settings())`` at import time, which
    needs DISCORD_WEBHOOK_URL present. ``get_settings`` is lru_cached, so the
    cache is cleared and the module reloaded to pick up this test's env.
    """
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
    monkeypatch.setenv("TESTING", "1")  # keep the scheduler from starting a real cron
    import app.config as config

    config.get_settings.cache_clear()
    import app.runner.app as appmod

    appmod = importlib.reload(appmod)
    return appmod, config.get_settings()


def test_healthz_reports_model_and_ok(monkeypatch):
    appmod, settings = _prepare(monkeypatch)
    monkeypatch.setattr(appmod, "_ollama_reachable", lambda s: True)

    client = TestClient(appmod.build_app(settings))
    resp = client.get("/healthz")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model"] == settings.ollama_model
    assert body["ollama_reachable"] is True


def test_play_invokes_run_cycle(monkeypatch):
    appmod, settings = _prepare(monkeypatch)
    calls: list[tuple[list[str], bool]] = []

    def _stub_run_cycle(s, game_types, force=False):
        calls.append((game_types, force))
        return []

    monkeypatch.setattr(appmod, "run_cycle", _stub_run_cycle)
    monkeypatch.setattr(appmod, "_ollama_reachable", lambda s: True)

    client = TestClient(appmod.build_app(settings))
    resp = client.post("/play?game=wordle")

    assert resp.status_code == 200
    assert calls == [(["wordle"], False)]


def test_play_both_with_force(monkeypatch):
    appmod, settings = _prepare(monkeypatch)
    calls: list[tuple[list[str], bool]] = []

    def _stub_run_cycle(s, game_types, force=False):
        calls.append((game_types, force))
        return []

    monkeypatch.setattr(appmod, "run_cycle", _stub_run_cycle)
    monkeypatch.setattr(appmod, "_ollama_reachable", lambda s: True)

    client = TestClient(appmod.build_app(settings))
    resp = client.post("/play?game=both&force=true")

    assert resp.status_code == 200
    assert calls == [(["wordle", "connections"], True)]


def test_play_rejects_unknown_game(monkeypatch):
    appmod, settings = _prepare(monkeypatch)
    monkeypatch.setattr(appmod, "_ollama_reachable", lambda s: True)

    client = TestClient(appmod.build_app(settings))
    resp = client.post("/play?game=sudoku")

    assert resp.status_code == 422


def test_play_forbidden_when_manual_trigger_disabled(monkeypatch):
    monkeypatch.setenv("MANUAL_TRIGGER_ENABLED", "false")
    appmod, settings = _prepare(monkeypatch)

    def _no_run(s, game_types, force=False):
        raise AssertionError("run_cycle must not run when manual trigger is disabled")

    monkeypatch.setattr(appmod, "run_cycle", _no_run)
    monkeypatch.setattr(appmod, "_ollama_reachable", lambda s: True)

    client = TestClient(appmod.build_app(settings))
    assert client.post("/play?game=wordle").status_code == 403


def test_play_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("PLAY_AUTH_TOKEN", "s3cret")
    appmod, settings = _prepare(monkeypatch)
    calls: list[tuple[list[str], bool]] = []

    def _stub_run_cycle(s, game_types, force=False):
        calls.append((game_types, force))
        return []

    monkeypatch.setattr(appmod, "run_cycle", _stub_run_cycle)
    monkeypatch.setattr(appmod, "_ollama_reachable", lambda s: True)

    client = TestClient(appmod.build_app(settings))
    assert client.post("/play?game=wordle").status_code == 401
    assert client.post("/play?game=wordle", headers={"X-Play-Token": "nope"}).status_code == 401
    resp = client.post("/play?game=wordle", headers={"X-Play-Token": "s3cret"})
    assert resp.status_code == 200
    assert calls == [(["wordle"], False)]


def test_ensure_model_pulls_when_absent(monkeypatch):
    appmod, settings = _prepare(monkeypatch)
    pulled: list[str] = []

    class _FakeModel:
        def __init__(self, name):
            self.model = name

    class _FakeList:
        def __init__(self, names):
            self.models = [_FakeModel(n) for n in names]

    class _FakeClient:
        def __init__(self, host=None):
            self.host = host

        def list(self):
            return _FakeList(["other:1b"])

        def pull(self, model):
            pulled.append(model)

    monkeypatch.setattr(appmod, "Client", _FakeClient)
    appmod.ensure_model(settings)

    assert pulled == [settings.ollama_model]


def test_ensure_model_noop_when_present(monkeypatch):
    appmod, settings = _prepare(monkeypatch)
    pulled: list[str] = []

    class _FakeModel:
        def __init__(self, name):
            self.model = name

    class _FakeList:
        def __init__(self, names):
            self.models = [_FakeModel(n) for n in names]

    class _FakeClient:
        def __init__(self, host=None):
            self.host = host

        def list(self):
            return _FakeList([settings.ollama_model])

        def pull(self, model):
            pulled.append(model)

    monkeypatch.setattr(appmod, "Client", _FakeClient)
    appmod.ensure_model(settings)

    assert pulled == []


def test_module_level_app_exists(monkeypatch):
    appmod, _ = _prepare(monkeypatch)

    assert isinstance(appmod.app, appmod.FastAPI)
    monkeypatch.setattr(appmod, "_ollama_reachable", lambda s: True)
    client = TestClient(appmod.app)
    assert client.get("/healthz").status_code == 200


def test_make_scheduler_registers_cron_job(monkeypatch):
    _, settings = _prepare(monkeypatch)
    from app.runner.scheduler import make_scheduler

    ran: list[int] = []
    sched = make_scheduler(settings, lambda: ran.append(1))
    try:
        jobs = sched.get_jobs()
        assert len(jobs) == 1
        trigger = jobs[0].trigger
        assert "minute='15'" in str(trigger)
        assert "hour='0'" in str(trigger)
    finally:
        if sched.running:
            sched.shutdown(wait=False)
