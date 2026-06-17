import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from ollama import Client

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
    """Pull the configured Ollama model if it is missing.

    No-op unless ``ollama_auto_pull`` is set. The local model list is matched
    against ``settings.ollama_model``; a missing model triggers a blocking pull.
    """
    if not settings.ollama_auto_pull:
        return
    client = Client(host=settings.ollama_host)
    present = {m.model for m in client.list().models}
    if settings.ollama_model not in present:
        logger.info("Pulling Ollama model %s", settings.ollama_model)
        client.pull(settings.ollama_model)


def _ollama_reachable(settings: Settings) -> bool:
    """Best-effort liveness probe for the Ollama backend used by /healthz."""
    try:
        Client(host=settings.ollama_host).list()
        return True
    except Exception:  # noqa: BLE001 - liveness probe never raises to the caller
        logger.warning("Ollama not reachable at %s", settings.ollama_host)
        return False


def run_cycle(
    settings: Settings, game_types: list[str], force: bool = False
) -> list[GameRecord]:
    """Play one or more games for today and return the persisted records.

    Opens the SQLite DB, builds a single LLMPlayer, computes today's date in
    ``schedule_tz``, then dispatches each requested game type to its runner.
    Runners return ``None`` when skipped (idempotency / not published); those
    are filtered out of the result.
    """
    conn = init_db(settings.db_path)
    try:
        repo = GameRepository(conn)
        player = LLMPlayer(settings)
        date = today_str(settings.schedule_tz)
        records: list[GameRecord] = []
        for game_type in game_types:
            if game_type == GameType.WORDLE.value:
                record = run_wordle(date, settings, player=player, repo=repo, force=force)
            elif game_type == GameType.CONNECTIONS.value:
                record = run_connections(date, settings, player=player, repo=repo, force=force)
            else:
                logger.warning("Unknown game type %r; skipping", game_type)
                continue
            if record is not None:
                records.append(record)
        return records
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
        return {
            "status": "ok",
            "model": settings.ollama_model,
            "ollama_reachable": _ollama_reachable(settings),
            "next_run": _next_run(scheduler),
        }

    @app.post("/play")
    def play(
        game: Literal["wordle", "connections", "both"] = "both",
        force: bool = False,
    ) -> dict[str, object]:
        game_types = _GAME_TYPES[game]
        records = run_cycle(settings, game_types, force=force)
        return {
            "played": game_types,
            "force": force,
            "records": [
                {
                    "game_type": r.game_type.value,
                    "puzzle_date": r.puzzle_date,
                    "outcome": r.outcome.value,
                }
                for r in records
            ],
        }

    return app


app = build_app(get_settings())
