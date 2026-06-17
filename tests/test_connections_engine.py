import random
from app.engines.models import Level, ConnectionsGroup, ConnectionsPuzzle, Outcome, SubmitResult
from app.engines.connections_engine import ConnectionsEngine

def _puzzle():
    groups = (
        ConnectionsGroup("A", ("A1", "A2", "A3", "A4"), Level.YELLOW),
        ConnectionsGroup("B", ("B1", "B2", "B3", "B4"), Level.GREEN),
        ConnectionsGroup("C", ("C1", "C2", "C3", "C4"), Level.BLUE),
        ConnectionsGroup("D", ("D1", "D2", "D3", "D4"), Level.PURPLE),
    )
    return ConnectionsPuzzle("2024-01-01", 204, "Wyna Liu", groups)

def test_correct_then_win():
    e = ConnectionsEngine(_puzzle(), rng=random.Random(0))
    assert e.submit(["A1", "A2", "A3", "A4"]) is SubmitResult.CORRECT
    assert e.submit(["B1", "B2", "B3", "B4"]) is SubmitResult.CORRECT
    assert e.submit(["C1", "C2", "C3", "C4"]) is SubmitResult.CORRECT
    assert e.submit(["D1", "D2", "D3", "D4"]) is SubmitResult.WIN
    assert e.status is Outcome.WIN

def test_one_away_costs_a_mistake():
    e = ConnectionsEngine(_puzzle(), rng=random.Random(0))
    assert e.submit(["A1", "A2", "A3", "B1"]) is SubmitResult.ONE_AWAY
    assert e.mistakes == 1

def test_duplicate_guess_no_mistake():
    e = ConnectionsEngine(_puzzle(), rng=random.Random(0))
    e.submit(["A1", "A2", "A3", "B1"])
    assert e.submit(["B1", "A3", "A2", "A1"]) is SubmitResult.ALREADY_GUESSED
    assert e.mistakes == 1

def test_four_mistakes_loss():
    e = ConnectionsEngine(_puzzle(), rng=random.Random(0))
    for wrong in (["A1","A2","B3","C4"],["A1","A2","B3","D4"],["A1","B2","C3","D4"],["A2","B2","C3","D4"]):
        last = e.submit(wrong)
    assert last is SubmitResult.LOSS and e.status is Outcome.LOSS
