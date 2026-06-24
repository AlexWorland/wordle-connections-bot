import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException

from app.config import Settings
from app.config import get_settings
from app.engines.models import GameRecord
from app.engines.models import GameType
from app.players.llm_player import LLMPlayer
from app.puzzles.dates import today_str
from app.runner.game_runner import run_connections
from app.runner.game_runner import run_wordle
from app.runner.scheduler import make_scheduler
from app.storage.db import GameRepository
from app.storage.db import init_db

logger = logging.getLogger(__name__)

_GAME_TYPES: dict[str, list[str]] = {
    GameType.WORDLE.value: [GameType.WORDLE.value],
    GameType.CONNECTIONS.value: [GameType.CONNECTIONS.value],
    "both": [GameType.WORDLE.value, GameType.CONNECTIONS.value],
}


def ensure_model(settings: Settings) -> None:
    """Pull the configured Ollama model if missing. No-op for llama.cpp backend."""
    if settings.llm_backend != "ollama" or not settings.ollama_auto_pull:
        return
    from ollama import Client
    client = Client(host=settings.ollama_host)
    present = {m.model for m in client.list().models}
    if settings.ollama_model not in present:
        logger.info("Pulling Ollama model %s", settings.ollama_model)
        client.pull(settings.ollama_model)


def _backend_reachable(settings: Settings) -> bool:
    """Best-effort liveness probe for whichever backend is configured."""
    try:
        if settings.llm_backend == "llama_cpp":
            import httpx
            r = httpx.get(f"{settings.llama_cpp_host.rstrip('/')}/health", timeout=3)
            return r.status_code == 200
        from ollama import Client
        Client(host=settings.ollama_host).list()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Backend not reachable (%s)", settings.llm_backend)
        return False


def run_cycle(
    settings: Settings, game_types: list[str], force: bool = False, dry_run: bool = False
) -> list[tuple[GameRecord, dict]]:
    """Play one or more games for today and return (record, embed) pairs.

    Opens the SQLite DB, builds a single LLMPlayer, computes today's date in
    ``schedule_tz``, then dispatches each requested game type to its runner.
    Runners return ``None`` when skipped (idempotency / not published); those
    are filtered out of the result. When ``dry_run`` is True the games are
    played but nothing is persisted or posted to Discord.
    """
    conn = init_db(settings.db_path)
    try:
        repo = GameRepository(conn)
        player = LLMPlayer(settings)
        date = today_str(settings.schedule_tz)
        results: list[tuple[GameRecord, dict, str | None]] = []
        for game_type in game_types:
            if game_type == GameType.WORDLE.value:
                result = run_wordle(date, settings, player=player, repo=repo, force=force, dry_run=dry_run)
            elif game_type == GameType.CONNECTIONS.value:
                result = run_connections(date, settings, player=player, repo=repo, force=force, dry_run=dry_run)
            else:
                logger.warning("Unknown game type %r; skipping", game_type)
                continue
            if result is not None:
                results.append(result)
        return results
    finally:
        conn.close()


def _next_run(scheduler: object) -> str | None:
    """Read the daily job's next run time, tolerating a not-yet-started scheduler.

    On a scheduler that has not been ``start()``-ed (e.g. under tests), the job
    exists but ``next_run_time`` is unavailable; treat that as ``None``.
    """
    get_job = getattr(scheduler, "get_job", None)
    if get_job is None:
        return None
    try:
        job = get_job("daily-cycle")
        run_time = getattr(job, "next_run_time", None)
    except (AttributeError, LookupError):
        return None
    return run_time.isoformat() if run_time is not None else None


def build_app(settings: Settings) -> FastAPI:
    def _scheduled_cycle() -> None:
        run_cycle(settings, settings.game_type_list)

    scheduler = make_scheduler(settings, _scheduled_cycle)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if not os.environ.get("TESTING"):
            ensure_model(settings)
            scheduler.start()
        try:
            yield
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=False)

    app = FastAPI(title="wordle-connections-bot", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        model = settings.llama_cpp_model or "default" if settings.llm_backend == "llama_cpp" else settings.ollama_model
        return {
            "status": "ok",
            "backend": settings.llm_backend,
            "model": model,
            "backend_reachable": _backend_reachable(settings),
            "next_run": _next_run(scheduler),
        }

    @app.post("/play")
    def play(
        game: Literal["wordle", "connections", "both"] = "both",
        force: bool = False,
        x_play_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        # Gate the state-changing endpoint: a deployment can disable it via config,
        # and (when a token is configured) every call must present it. force=true is
        # destructive (delete + re-post), so it rides the same auth path.
        if not settings.manual_trigger_enabled:
            raise HTTPException(status_code=403, detail="Manual trigger is disabled")
        if settings.play_auth_token and not hmac.compare_digest(
            x_play_token or "", settings.play_auth_token
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing X-Play-Token")
        game_types = _GAME_TYPES[game]
        results = run_cycle(settings, game_types, force=force)
        return {
            "played": game_types,
            "force": force,
            "records": [
                {
                    "game_type": r.game_type.value,
                    "puzzle_date": r.puzzle_date,
                    "outcome": r.outcome.value,
                }
                for r, _, _pm in results
            ],
        }

    @app.get("/preview")
    def preview(
        game: Literal["wordle", "connections", "both"] = "both",
    ) -> dict[str, object]:
        """Play today's game(s) and return the Discord embed payload without posting."""
        game_types = _GAME_TYPES[game]
        results = run_cycle(settings, game_types, dry_run=True)
        return {
            "game_types": game_types,
            "results": [
                {
                    "game_type": r.game_type.value,
                    "puzzle_date": r.puzzle_date,
                    "outcome": r.outcome.value,
                    "num_guesses": r.num_guesses,
                    "num_mistakes": r.num_mistakes,
                    "turns": [
                        {"guess": t.guess, "reasoning": t.reasoning}
                        for t in r.turns
                    ],
                    "postmortem": postmortem,
                    "embed": embed,
                }
                for r, embed, postmortem in results
            ],
        }

    return app


app = build_app(get_settings())
