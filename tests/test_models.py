from app.engines.models import Level, LEVEL_EMOJI, Mark, WORDLE_EMOJI, ConnectionsGroup

def test_level_ordering_and_emoji():
    assert list(Level) == [Level.YELLOW, Level.GREEN, Level.BLUE, Level.PURPLE]
    assert LEVEL_EMOJI[Level.PURPLE] == "🟪"
    assert WORDLE_EMOJI[Mark.GREEN] == "🟩"

def test_group_word_set():
    g = ConnectionsGroup(title="X", words=("A", "B", "C", "D"), level=Level.YELLOW)
    assert set(g.words) == {"A", "B", "C", "D"}
