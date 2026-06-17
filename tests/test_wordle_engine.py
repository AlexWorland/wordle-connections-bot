import pytest
from app.engines.models import Mark
from app.engines.wordle_engine import score_guess, WordleEngine
from app.engines.models import WordlePuzzle

G, Y, X = Mark.GREEN, Mark.YELLOW, Mark.GRAY

@pytest.mark.parametrize("guess,solution,expected", [
    ("alley", "leafy", [Y, Y, X, Y, G]),   # 2nd L gray: solution has one L
    ("eerie", "elder", [G, Y, Y, X, X]),   # 3rd E gray: only two E's
    ("speed", "erase", [Y, X, Y, Y, X]),
    ("token", "token", [G, G, G, G, G]),   # win
])
def test_score_guess_duplicates(guess, solution, expected):
    assert score_guess(guess, solution) == expected

def test_engine_win_and_validation():
    eng = WordleEngine(WordlePuzzle("2026-06-17", 1824, "token", "Tracy Bennett"),
                       allowed={"crane", "token"})
    assert eng.validate_guess("crane") is None
    eng.apply_guess("crane")
    assert eng.status is None
    eng.apply_guess("token")
    assert eng.solved and eng.status.value == "win"

def test_validate_rejects_bad_guesses():
    eng = WordleEngine(WordlePuzzle("2026-06-17", 1824, "token", None), allowed={"crane"})
    assert eng.validate_guess("toolong").reason == "length"
    assert eng.validate_guess("zzzzz").reason == "not_in_dictionary"
