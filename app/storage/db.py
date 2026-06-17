import json
import sqlite3

from app.engines.models import GameRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_type     TEXT    NOT NULL,
    puzzle_date   TEXT    NOT NULL,
    puzzle_number INTEGER,
    puzzle_id     INTEGER,
    model         TEXT    NOT NULL,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    outcome       TEXT    NOT NULL,
    num_guesses   INTEGER NOT NULL,
    num_mistakes  INTEGER NOT NULL,
    answer_json   TEXT    NOT NULL,
    UNIQUE (game_type, puzzle_date, model)
);

CREATE TABLE IF NOT EXISTS turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    turn_index    INTEGER NOT NULL,
    guess_json    TEXT    NOT NULL,
    feedback_json TEXT    NOT NULL,
    reasoning     TEXT,
    was_valid     INTEGER NOT NULL,
    retries       INTEGER NOT NULL DEFAULT 0
);
"""


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


class GameRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def exists(self, game_type: str, date: str, model: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM games WHERE game_type = ? AND puzzle_date = ? AND model = ?",
            (game_type, date, model),
        ).fetchone()
        return row is not None

    def delete(self, game_type: str, date: str, model: str) -> None:
        self.conn.execute(
            "DELETE FROM games WHERE game_type = ? AND puzzle_date = ? AND model = ?",
            (game_type, date, model),
        )
        self.conn.commit()

    def save(self, record: GameRecord) -> int:
        cur = self.conn.execute(
            """INSERT INTO games (game_type, puzzle_date, puzzle_number, puzzle_id, model,
                                  started_at, finished_at, outcome, num_guesses, num_mistakes,
                                  answer_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.game_type.value,
                record.puzzle_date,
                record.puzzle_number,
                record.puzzle_id,
                record.model,
                record.started_at,
                record.finished_at,
                record.outcome.value,
                record.num_guesses,
                record.num_mistakes,
                record.answer_json,
            ),
        )
        game_id = cur.lastrowid
        assert game_id is not None
        for turn in record.turns:
            self.conn.execute(
                """INSERT INTO turns (game_id, turn_index, guess_json, feedback_json,
                                      reasoning, was_valid, retries)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    game_id,
                    turn.turn_index,
                    json.dumps(turn.guess),
                    json.dumps(turn.feedback),
                    turn.reasoning,
                    1,
                    turn.retries,
                ),
            )
        self.conn.commit()
        return game_id
