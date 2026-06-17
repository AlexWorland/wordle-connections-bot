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
        # Collect ALL violations in one pass so the corrective message is complete.
        violations: list[str] = []
        for word, marks in self.guess_rows:
            for i, m in enumerate(marks):
                if m is Mark.GREEN and g[i] != word[i]:
                    violations.append(f"position {i + 1} must be '{word[i].upper()}'")
                elif m is Mark.YELLOW and word[i] not in g:
                    violations.append(f"must include '{word[i].upper()}'")
        if not violations:
            return None
        return MoveProblem(
            "hard_mode",
            f'"{g.upper()}" violates hard mode: {"; ".join(violations)}. '
            f"Your guess must satisfy ALL of these constraints — check the REQUIRED section above.",
        )

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

        # Per-letter breakdown so the model never has to count positions
        lines = [header, ""]
        for word, marks in self.guess_rows:
            parts = [f"{ch.upper()}{WORDLE_EMOJI[m]}" for ch, m in zip(word, marks)]
            lines.append("  " + "  ".join(parts))

        # Synthesised knowledge summary — green, yellow, gray sets
        greens: dict[int, str] = {}   # pos -> letter
        yellows: dict[str, set[int]] = {}  # letter -> positions where it ISN'T
        grays: set[str] = set()
        for word, marks in self.guess_rows:
            for i, (ch, m) in enumerate(zip(word, marks)):
                if m is Mark.GREEN:
                    greens[i] = ch
                elif m is Mark.YELLOW:
                    yellows.setdefault(ch, set()).add(i)
                elif m is Mark.GRAY and ch not in greens.values() and ch not in yellows:
                    grays.add(ch)

        lines.append("")
        lines.append("What we know:")
        if greens:
            g_parts = [f"position {i+1}={ch.upper()}" for i, ch in sorted(greens.items())]
            lines.append(f"  🟩 Confirmed: {', '.join(g_parts)}")
        if yellows:
            y_parts = [
                f"{ch.upper()} (not at position{'s' if len(pos) > 1 else ''} {', '.join(str(p+1) for p in sorted(pos))})"
                for ch, pos in sorted(yellows.items())
            ]
            lines.append(f"  🟨 In the word: {', '.join(y_parts)}")
        if grays:
            lines.append(f"  ⬜ Not in word: {', '.join(sorted(ch.upper() for ch in grays))}")

        # Hard-mode checklist — explicit per-letter requirements the model must satisfy
        if greens or yellows:
            lines.append("")
            lines.append("⚠️  REQUIRED for your next guess (hard mode):")
            for i, ch in sorted(greens.items()):
                lines.append(f"  • Position {i+1} MUST be '{ch.upper()}'")
            for ch, bad_positions in sorted(yellows.items()):
                pos_str = ", ".join(str(p + 1) for p in sorted(bad_positions))
                lines.append(
                    f"  • MUST include '{ch.upper()}' (NOT at position{'s' if len(bad_positions) > 1 else ''} {pos_str})"
                )

        return "\n".join(lines)
