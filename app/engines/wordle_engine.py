from collections import Counter
from importlib.resources import files
from app.engines.models import Mark, Outcome, WordlePuzzle, MoveProblem, WORDLE_EMOJI

MAX_GUESSES = 6
WORD_LEN = 5


def score_guess(guess: str, solution: str) -> list[Mark]:
    result = [Mark.GRAY] * WORD_LEN
    remaining = Counter(solution)
    for i in range(WORD_LEN):                       # pass 1: greens consume counts
        if guess[i] == solution[i]:
            result[i] = Mark.GREEN
            remaining[guess[i]] -= 1
    for i in range(WORD_LEN):                        # pass 2: yellows from remainder
        if result[i] is Mark.GREEN:
            continue
        if remaining[guess[i]] > 0:
            result[i] = Mark.YELLOW
            remaining[guess[i]] -= 1
    return result


def load_allowed_guesses(path: str | None = None) -> set[str]:
    if path:
        text = open(path, encoding="utf-8").read()
    else:
        text = (files("app.wordlists") / "allowed_guesses.txt").read_text(encoding="utf-8")
    return {w.strip().lower() for w in text.splitlines() if w.strip()}


class WordleEngine:
    MAX_GUESSES = MAX_GUESSES

    def __init__(self, puzzle: WordlePuzzle, allowed: set[str], hard_mode: bool = False) -> None:
        self.puzzle = puzzle
        self.solution = puzzle.solution.lower()
        self.allowed = allowed
        self.hard_mode = hard_mode
        self.guess_rows: list[tuple[str, list[Mark]]] = []

    @property
    def attempts_used(self) -> int:
        return len(self.guess_rows)

    @property
    def solved(self) -> bool:
        return bool(self.guess_rows) and all(m is Mark.GREEN for m in self.guess_rows[-1][1])

    @property
    def status(self) -> Outcome | None:
        if self.solved:
            return Outcome.WIN
        if self.attempts_used >= MAX_GUESSES:
            return Outcome.LOSS
        return None

    def validate_guess(self, guess: str) -> MoveProblem | None:
        g = guess.strip().lower()
        if len(g) != WORD_LEN or not g.isalpha():
            return MoveProblem("length", f'"{guess}" is not a 5-letter word. Reply with exactly 5 letters a-z.')
        if g not in self.allowed:
            return MoveProblem("not_in_dictionary", f'"{guess}" is not in the word list. Pick a different real 5-letter word.')
        if g in (row[0] for row in self.guess_rows):
            return MoveProblem("repeat", f'You already guessed "{guess}". Pick a different word.')
        if self.hard_mode and (hm := self._hard_mode_problem(g)):
            return hm
        return None

    def _hard_mode_problem(self, g: str) -> MoveProblem | None:
        for word, marks in self.guess_rows:
            for i, m in enumerate(marks):
                if m is Mark.GREEN and g[i] != word[i]:
                    return MoveProblem("hard_mode", f"Position {i + 1} must be '{word[i].upper()}'.")
                if m is Mark.YELLOW and word[i] not in g:
                    return MoveProblem("hard_mode", f"Your guess must contain '{word[i].upper()}'.")
        return None

    def apply_guess(self, guess: str) -> list[Mark]:
        g = guess.strip().lower()
        marks = score_guess(g, self.solution)
        self.guess_rows.append((g, marks))
        return marks

    def render_state(self) -> str:
        used = self.attempts_used
        remaining = MAX_GUESSES - used
        turn = used + 1
        header = f"Turn {turn} of {MAX_GUESSES} — {remaining} guess{'es' if remaining != 1 else ''} remaining"
        if not self.guess_rows:
            return f"{header}\nNo guesses yet."
        lines = [header]
        for word, marks in self.guess_rows:
            grid = "".join(WORDLE_EMOJI[m] for m in marks)
            lines.append(f"  {word.upper()}  {grid}")
        return "\n".join(lines)
