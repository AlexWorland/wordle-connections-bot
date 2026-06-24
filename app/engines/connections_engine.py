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
        self._forfeited = False
        self.solved_groups: list[ConnectionsGroup] = []
        self._past: set[frozenset[str]] = set()
        self.guess_rows: list[list[str]] = []
        self._wrong_attempts: list[tuple[list[str], SubmitResult]] = []  # (words, result) for wrong guesses only
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
        if self.mistakes >= MAX_MISTAKES or self._forfeited:
            return Outcome.LOSS
        return None

    def forfeit(self) -> None:
        """Mark the game lost when the player is cornered (can only repeat prior
        selections after retries). Counts as a loss, not an error — real moves
        were made and the player simply ran out of viable distinct selections."""
        self._forfeited = True

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
        result = SubmitResult.ONE_AWAY if best == 3 else SubmitResult.INCORRECT
        self._wrong_attempts.append((list(words), result))
        if self.mistakes >= MAX_MISTAKES:
            return SubmitResult.LOSS
        return result

    def _unsolved(self) -> list[ConnectionsGroup]:
        solved = {g.title for g in self.solved_groups}
        return [g for g in self.puzzle.groups if g.title not in solved]

    def render_state(self) -> str:
        mistakes_left = MAX_MISTAKES - self.mistakes
        header = (
            f"{len(self.solved_groups)}/4 groups solved. "
            f"You have made {self.mistakes} wrong answer{'s' if self.mistakes != 1 else ''} "
            f"out of {MAX_MISTAKES} allowed — "
            f"{mistakes_left} wrong answer{'s' if mistakes_left != 1 else ''} remaining before you lose."
        )
        lines = [header, ""]
        if mistakes_left == 1:
            lines.append(
                "⚠️  ONLY 1 WRONG ANSWER LEFT. Submit a group ONLY if you are highly "
                "confident. Solving any single group reveals more by elimination."
            )
            lines.append("")

        # Remaining words
        words = sorted(self.remaining_words)
        lines.append(f"Remaining words ({len(words)}): {', '.join(words)}")

        # Solved groups
        if self.solved_groups:
            lines.append("")
            lines.append("Solved groups:")
            for g in self.solved_groups:
                lines.append(f"  ✅ {g.title}: {', '.join(sorted(g.words))}")

        # Previous wrong attempts — with result label and ONE_AWAY deduction hint
        if self._wrong_attempts:
            lines.append("")
            lines.append("Previous wrong attempts:")
            for attempt_words, result in self._wrong_attempts:
                if result is SubmitResult.ONE_AWAY:
                    lines.append(f"  🔴 ONE_AWAY: {', '.join(attempt_words)}")
                    lines.append(
                        "       ↳ Exactly 3 of these 4 belong to the same group, but one "
                        "is wrong. Do NOT immediately re-guess a near-variant — pivot to a "
                        "group you are MORE sure of first; elimination often reveals the intruder."
                    )
                else:
                    lines.append(f"  ❌ INCORRECT: {', '.join(attempt_words)}")

        # Hard constraint: do not repeat
        if self._wrong_attempts:
            lines.append("")
            lines.append("⚠️  DO NOT repeat any of the above exact sets of 4 words.")

        return "\n".join(lines)

    def render_share_grid(self) -> str:
        return "\n".join("".join(LEVEL_EMOJI[self._word_to_group[w].level] for w in row)
                         for row in self.guess_rows)
