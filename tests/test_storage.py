from app.storage.db import init_db, GameRepository
from app.engines.models import GameRecord, TurnRecord, GameType, Outcome


def _rec(model="gemma4:12b", outcome=Outcome.WIN):
    return GameRecord(GameType.WORDLE, "2026-06-17", 1824, 1735, model, "t0", "t1",
                      outcome, 3, 2, '{"solution":"token"}',
                      [TurnRecord(0, "crane", "⬜⬜⬜⬜⬜", "r", 0)])


def test_save_and_exists(tmp_path):
    conn = init_db(str(tmp_path / "g.db"))
    repo = GameRepository(conn)
    assert not repo.exists("wordle", "2026-06-17", "gemma4:12b")
    repo.save(_rec())
    assert repo.exists("wordle", "2026-06-17", "gemma4:12b")


def test_force_replace(tmp_path):
    conn = init_db(str(tmp_path / "g.db"))
    repo = GameRepository(conn)
    repo.save(_rec(outcome=Outcome.LOSS))
    repo.delete("wordle", "2026-06-17", "gemma4:12b")
    repo.save(_rec(outcome=Outcome.WIN))   # no UNIQUE violation
    assert repo.exists("wordle", "2026-06-17", "gemma4:12b")
