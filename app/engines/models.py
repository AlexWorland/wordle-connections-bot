from dataclasses import dataclass, field
from enum import Enum, IntEnum


class Mark(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    GRAY = "gray"


class Level(IntEnum):
    YELLOW = 0
    GREEN = 1
    BLUE = 2
    PURPLE = 3


class GameType(str, Enum):
    WORDLE = "wordle"
    CONNECTIONS = "connections"


class Outcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    ERRORED = "errored"


class SubmitResult(str, Enum):
    CORRECT = "correct"
    WIN = "win"
    INCORRECT = "incorrect"
    ONE_AWAY = "one_away"
    LOSS = "loss"
    ALREADY_GUESSED = "already_guessed"


WORDLE_EMOJI: dict[Mark, str] = {
    Mark.GREEN: "🟩",
    Mark.YELLOW: "🟨",
    Mark.GRAY: "⬜",
}

LEVEL_EMOJI: dict[Level, str] = {
    Level.YELLOW: "🟨",
    Level.GREEN: "🟩",
    Level.BLUE: "🟦",
    Level.PURPLE: "🟪",
}


@dataclass(frozen=True)
class WordlePuzzle:
    date: str
    number: int | None
    solution: str
    editor: str | None


@dataclass(frozen=True)
class ConnectionsGroup:
    title: str
    words: tuple[str, ...]
    level: Level


@dataclass(frozen=True)
class ConnectionsPuzzle:
    date: str
    number: int
    editor: str | None
    groups: tuple[ConnectionsGroup, ...]


@dataclass(frozen=True)
class MoveProblem:
    reason: str
    feedback: str


@dataclass
class TurnRecord:
    turn_index: int
    guess: str
    feedback: str
    reasoning: str
    retries: int


@dataclass
class GameRecord:
    game_type: GameType
    puzzle_date: str
    puzzle_number: int | None
    puzzle_id: int | None
    model: str
    started_at: str
    finished_at: str | None
    outcome: Outcome
    num_guesses: int
    num_mistakes: int
    answer_json: str
    turns: list[TurnRecord] = field(default_factory=list)
