import json
import logging
import random
from datetime import datetime
from datetime import timezone

import httpx

from app.config import Settings
from app.engines.connections_engine import ConnectionsEngine
from app.engines.models import GameRecord
from app.engines.models import GameType
from app.engines.models import Outcome
from app.engines.models import WORDLE_EMOJI
from app.engines.wordle_engine import WordleEngine
from app.engines.wordle_engine import load_allowed_guesses
from app.output.discord_webhook import build_connections_embed
from app.output.discord_webhook import build_wordle_embed
from app.output.discord_webhook import post_embed
from app.players.llm_player import InvalidMoveExhausted
from app.players.llm_player import LLMPlayer
from app.puzzles.connections_source import fetch_connections
from app.puzzles.wordle_source import PuzzleNotPublished
from app.puzzles.wordle_source import fetch_wordle
from app.storage.db import GameRepository

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skip_for_idempotency(
    repo: GameRepository, game_type: str, date: str, model: str, force: bool
) -> bool:
    """Return True when an existing record means this run is a no-op.

    When ``force`` is set, any existing record is deleted first (cascade removes
    its turns) so the replay can re-INSERT without violating the UNIQUE triple.
    """
    if repo.exists(game_type, date, model):
        if not force:
            logger.info("Skipping %s %s for %s: already recorded", game_type, date, model)
            return True
        repo.delete(game_type, date, model)
    return False


def run_wordle(
    date: str,
    settings: Settings,
    *,
    player: LLMPlayer,
    repo: GameRepository,
    http: httpx.Client | None = None,
    force: bool = False,
) -> GameRecord | None:
    model = settings.ollama_model
    if _skip_for_idempotency(repo, GameType.WORDLE.value, date, model, force):
        return None

    try:
        puzzle = fetch_wordle(date, settings, client=http)
    except PuzzleNotPublished:
        logger.warning("Wordle for %s is not published", date)
        return None

    engine = WordleEngine(
        puzzle, allowed=load_allowed_guesses(), hard_mode=settings.wordle_hard_mode
    )
    started_at = _now()
    answer_json = json.dumps({"solution": puzzle.solution})

    try:
        turns = player.play_wordle(engine)
    except InvalidMoveExhausted:
        logger.error("Wordle for %s errored: invalid-move retries exhausted", date)
        record = GameRecord(
            game_type=GameType.WORDLE,
            puzzle_date=puzzle.date,
            puzzle_number=puzzle.number,
            puzzle_id=puzzle.number,
            model=model,
            started_at=started_at,
            finished_at=_now(),
            outcome=Outcome.ERRORED,
            num_guesses=engine.attempts_used,
            num_mistakes=engine.attempts_used,
            answer_json=answer_json,
            turns=[],
        )
        repo.save(record)
        if settings.post_on_fetch_failure:
            _post_wordle(record, engine, puzzle, settings, http)
        return record

    won = engine.status is Outcome.WIN
    record = GameRecord(
        game_type=GameType.WORDLE,
        puzzle_date=puzzle.date,
        puzzle_number=puzzle.number,
        puzzle_id=puzzle.number,
        model=model,
        started_at=started_at,
        finished_at=_now(),
        outcome=engine.status or Outcome.LOSS,
        num_guesses=engine.attempts_used,
        num_mistakes=engine.attempts_used - 1 if won else engine.attempts_used,
        answer_json=answer_json,
        turns=turns,
    )
    repo.save(record)
    _post_wordle(record, engine, puzzle, settings, http)
    return record


def _post_wordle(
    record: GameRecord,
    engine: WordleEngine,
    puzzle,
    settings: Settings,
    http: httpx.Client | None,
) -> None:
    marks_rows = [
        "".join(WORDLE_EMOJI[m] for m in marks) for _, marks in engine.guess_rows
    ]
    embed = build_wordle_embed(
        number=puzzle.number,
        marks_rows=marks_rows,
        solution=puzzle.solution,
        model=record.model,
        won=record.outcome is Outcome.WIN,
    )
    post_embed(embed, settings, client=http)


def run_connections(
    date: str,
    settings: Settings,
    *,
    player: LLMPlayer,
    repo: GameRepository,
    http: httpx.Client | None = None,
    rng: random.Random | None = None,
    force: bool = False,
) -> GameRecord | None:
    model = settings.ollama_model
    if _skip_for_idempotency(repo, GameType.CONNECTIONS.value, date, model, force):
        return None

    try:
        puzzle = fetch_connections(date, settings, client=http)
    except PuzzleNotPublished:
        logger.warning("Connections for %s is not published", date)
        return None

    engine = ConnectionsEngine(puzzle, rng=rng or random.Random())
    started_at = _now()
    answer_json = json.dumps(
        {g.title: list(g.words) for g in puzzle.groups}
    )

    try:
        turns = player.play_connections(engine)
    except InvalidMoveExhausted:
        logger.error("Connections for %s errored: invalid-move retries exhausted", date)
        record = GameRecord(
            game_type=GameType.CONNECTIONS,
            puzzle_date=puzzle.date,
            puzzle_number=puzzle.number,
            puzzle_id=puzzle.number,
            model=model,
            started_at=started_at,
            finished_at=_now(),
            outcome=Outcome.ERRORED,
            num_guesses=len(engine.guess_rows),
            num_mistakes=engine.mistakes,
            answer_json=answer_json,
            turns=[],
        )
        repo.save(record)
        if settings.post_on_fetch_failure:
            _post_connections(record, engine, puzzle, settings, http)
        return record

    record = GameRecord(
        game_type=GameType.CONNECTIONS,
        puzzle_date=puzzle.date,
        puzzle_number=puzzle.number,
        puzzle_id=puzzle.number,
        model=model,
        started_at=started_at,
        finished_at=_now(),
        outcome=engine.status or Outcome.LOSS,
        num_guesses=len(engine.guess_rows),
        num_mistakes=engine.mistakes,
        answer_json=answer_json,
        turns=turns,
    )
    repo.save(record)
    _post_connections(record, engine, puzzle, settings, http)
    return record


def _post_connections(
    record: GameRecord,
    engine: ConnectionsEngine,
    puzzle,
    settings: Settings,
    http: httpx.Client | None,
) -> None:
    groups_text = "\n".join(
        f"{g.title}: {', '.join(g.words)}" for g in puzzle.groups
    )
    embed = build_connections_embed(
        number=puzzle.number,
        grid=engine.render_share_grid(),
        groups_text=groups_text,
        model=record.model,
        mistakes=record.num_mistakes,
        won=record.outcome is Outcome.WIN,
    )
    post_embed(embed, settings, client=http)
