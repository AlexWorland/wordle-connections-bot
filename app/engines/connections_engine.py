import random
from app.engines.models import (ConnectionsGroup, ConnectionsPuzzle, LEVEL_EMOJI,
                                 MoveProblem, Outcome, SubmitResult)

MAX_MISTAKES = 4


class ConnectionsEngine:
    MAX_MISTAKES = MAX_MISTAKES

    def __init__(self, puzzle: ConnectionsPuzzle, rng: random.Random | None = None) -> None:
        self.puzzle = puzzle
        self._rng = rng or random.Random()
        self._word_to_group = {w: g for g in puzzle.groups for w in g.words}
        self.mistakes = 0
        self.solved_groups: list[ConnectionsGroup] = []
        self._past: set[frozenset[str]] = set()
        self.guess_rows: list[list[str]] = []
        self._order = list(self._word_to_group)
        self._rng.shuffle(self._order)

    @property
    def remaining_words(self) -> set[str]:
        solved = {w for g in self.solved_groups for w in g.words}
        return set(self._word_to_group) - solved

    @property
    def status(self) -> Outcome | None:
        if len(self.solved_groups) == 4:
            return Outcome.WIN
        if self.mistakes >= MAX_MISTAKES:
            return Outcome.LOSS
        return None

    def validate_selection(self, words: list[str]) -> MoveProblem | None:
        uniq = set(words)
        if len(words) != 4 or len(uniq) != 4:
            return MoveProblem("size", "Select exactly 4 distinct words.")
        bad = uniq - self.remaining_words
        if bad:
            return MoveProblem("not_in_pool", f"These are not available: {sorted(bad)}. Choose from remaining words.")
        if frozenset(uniq) in self._past:
            return MoveProblem("repeat", "You already tried that exact set of 4. Try a different combination.")
        return None

    def submit(self, words: list[str]) -> SubmitResult:
        sel = frozenset(words)
        if sel in self._past:
            return SubmitResult.ALREADY_GUESSED
        self._past.add(sel)
        self.guess_rows.append(list(words))
        best = max(len(sel & set(g.words)) for g in self._unsolved())
        if best == 4:
            matched = next(g for g in self._unsolved() if sel == set(g.words))
            self.solved_groups.append(matched)
            return SubmitResult.WIN if len(self.solved_groups) == 4 else SubmitResult.CORRECT
        self.mistakes += 1
        if self.mistakes >= MAX_MISTAKES:
            return SubmitResult.LOSS
        return SubmitResult.ONE_AWAY if best == 3 else SubmitResult.INCORRECT

    def _unsolved(self) -> list[ConnectionsGroup]:
        solved = {g.title for g in self.solved_groups}
        return [g for g in self.puzzle.groups if g.title not in solved]

    def render_state(self) -> str:
        turn = len(self.guess_rows) + 1
        solved_count = len(self.solved_groups)
        mistakes_left = MAX_MISTAKES - self.mistakes
        header = (
            f"Turn {turn} — "
            f"{solved_count}/4 groups solved, "
            f"{self.mistakes} mistake{'s' if self.mistakes != 1 else ''} used, "
            f"{mistakes_left} mistake{'s' if mistakes_left != 1 else ''} remaining"
        )
        words = sorted(self.remaining_words)
        lines = [header, f"Remaining words ({len(words)}): {', '.join(words)}"]
        if self.solved_groups:
            lines.append("Solved groups:")
            for g in self.solved_groups:
                lines.append(f"  ✅ {g.title}: {', '.join(g.words)}")
        if self.guess_rows:
            lines.append("Already tried (wrong): " +
                         "; ".join(", ".join(r) for r in self.guess_rows
                                   if frozenset(r) not in {frozenset(g.words) for g in self.solved_groups}
                                   ) or "none")
        return "\n".join(lines)

    def render_share_grid(self) -> str:
        return "\n".join("".join(LEVEL_EMOJI[self._word_to_group[w].level] for w in row)
                         for row in self.guess_rows)
