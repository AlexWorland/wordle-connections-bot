# Wordle & Connections LLM Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **A ready-to-run Claude Code Workflow that executes this entire plan is included in the final section — see "Workflow Execution".**

**Goal:** Build a Dockerized Python service that auto-plays the day's real NYT Wordle and Connections puzzles with a local Ollama model and posts results to a Discord webhook.

**Architecture:** A single long-running container (`bot`) runs an APScheduler cron job + a tiny FastAPI surface (`/healthz`, `POST /play`); it fetches live puzzles from NYT JSON endpoints, models each game in pure engines, drives a local Ollama model (`gemma4:12b`, configurable) through a structured-output guess loop, persists every game to SQLite tagged by model, and posts a final emoji share-grid (answer spoiler-tagged) to Discord. A separate `ollama` container serves inference (CPU on macOS, GPU on Linux via a compose override).

**Tech Stack:** Python 3.12, `ollama`, `pydantic` + `pydantic-settings`, `fastapi` + `uvicorn`, `apscheduler`, `httpx`, `tenacity`, stdlib `sqlite3`; `pytest` + `respx` + `ruff` + `mypy`; Docker + Compose.

**Spec:** `docs/superpowers/specs/2026-06-17-wordle-connections-discord-bot-design.md`

## Global Constraints

Every task's requirements implicitly include these (verbatim from the spec):

- **Python 3.12+.** Engines are **pure** (no network/clock/RNG except an injected shuffle) so they are deterministically testable.
- **Wordle scoring MUST use the two-pass multiset algorithm** (greens consume letter counts before yellows are assigned). Naive `'yellow if ch in solution'` is a defect.
- **Connections difficulty has NO field in the data** — derive color from category array order: `categories[0]`→YELLOW(0) … `categories[3]`→PURPLE(3).
- **Ollama `format=` schemas must NOT use `pattern`/regex** (Ollama 500s). Enforce length/charset/membership in Python.
- **`OLLAMA_HOST` is the compose service URL** `http://ollama:11434`, never `localhost`.
- **Invalid LLM guesses trigger a corrective re-prompt and do NOT consume a game turn.** `MAX_INVALID_RETRIES` (default 10) is an infinite-loop backstop only; exhaustion ⇒ outcome `errored`.
- **`DISCORD_WEBHOOK_URL` is a secret** — never logged, never committed; `.env` is git-ignored.
- **Discord:** embed `color` is a **decimal int**; spoilers `||...||` do **not** work inside code blocks; set `allowed_mentions={"parse": []}`.
- **Dates computed in `America/New_York`** (default `SCHEDULE_TZ`) to align with NYT rollover. Send a browser `User-Agent` to NYT (Connections is behind DataDome).
- **Idempotency:** SQLite `UNIQUE(game_type, puzzle_date, model)`; a re-run for the same day+model is a no-op unless `force=true`.
- **Tooling gates per task:** `ruff check .` clean, `mypy app` clean, `pytest` green.

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `pyproject.toml`, `.env.example`, `conftest.py` | Deps, tooling, env template, shared test fixtures | T1 |
| `app/config.py` | `Settings` (pydantic-settings); secret redaction | T1 |
| `app/engines/models.py` | All domain types + enums (pure) | T2 |
| `app/engines/wordle_engine.py`, `app/wordlists/allowed_guesses.txt` | Two-pass scoring, validity, state, hard mode | T3 |
| `app/engines/connections_engine.py` | Overlap/one-away/lock, share grid | T4 |
| `app/puzzles/dates.py`, `app/puzzles/wordle_source.py` | Today-in-ET; fetch+parse Wordle JSON | T5 |
| `app/puzzles/connections_source.py` | Fetch+parse Connections JSON; level-by-order | T6 |
| `app/players/llm_player.py`, `app/players/prompts/` | Ollama wrapper, schemas, corrective retry loop | T7 |
| `app/storage/db.py`, `app/storage/stats.py` | SQLite schema, repositories, idempotency, stats | T8 |
| `app/output/discord_webhook.py` | Embed builder, grid renderer, spoilers, backoff | T9 |
| `app/runner/game_runner.py` | Orchestrate one game end-to-end | T10 |
| `app/runner/scheduler.py`, `app/runner/app.py` | APScheduler + FastAPI (`/healthz`, `/play`) | T11 |
| `Dockerfile`, `docker-compose.yml`, `docker-compose.gpu.yml`, `entrypoint.sh` | Containerization, GPU override, model pull | T12 |

## Dependency DAG (drives the Workflow phases)

```
T1 scaffold/config ─┬─> T2 models ─┬─> T3 wordle-engine ──┐
                    │              ├─> T4 conn-engine  ───┤
                    │              ├─> T5 wordle-source    │
                    │              ├─> T6 conn-source      ├─> T7 llm-player ─┐
                    │              ├─> T8 storage          │                 │
                    │              └─> T9 discord          │                 │
                    │                                      │                 ▼
                    └──────────────────────────────────────┴────────> T10 runner ─> T11 app ─> T12 docker
```

- **Phase A (serial):** T1 → T2 (foundation; everything depends on these).
- **Phase B (parallel, 6 agents):** T3, T4, T5, T6, T8, T9 — disjoint files, depend only on T2/T1.
- **Phase B2 (serial):** T7 (needs T3 + T4 validators).
- **Phase C (serial):** T10 → T11 (integration).
- **Phase D (serial):** T12 (docker + smoke).

**Parallel-safety rule:** in Phase B, build agents **write files + run their own task's pytest but DO NOT run git**. A serial **commit agent** after the phase runs the full suite and commits each module in dependency order. (Concurrent `git commit` races on `index.lock`.)

---

## Task 1: Project scaffold & config

**Files:**
- Create: `pyproject.toml`, `.env.example`, `conftest.py`, `app/__init__.py`, `app/config.py`, and empty `__init__.py` in every `app/` subpackage.
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings` (pydantic-settings model with the §6 fields), `app.config.get_settings() -> Settings`, `Settings.redacted_webhook() -> str`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "wordle-connections-bot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "ollama>=0.4",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "fastapi>=0.111",
  "uvicorn>=0.30",
  "apscheduler>=3.10",
  "httpx>=0.27",
  "tenacity>=8.3",
]

[project.optional-dependencies]
dev = ["pytest>=8", "respx>=0.21", "ruff>=0.5", "mypy>=1.10"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write the failing test** (`tests/test_config.py`)

```python
import importlib

def test_settings_load_with_required_webhook(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/abc")
    from app.config import Settings
    s = Settings()
    assert s.ollama_host == "http://ollama:11434"
    assert s.ollama_model == "gemma4:12b"
    assert s.schedule_tz == "America/New_York"
    assert s.max_invalid_retries == 10

def test_redacted_webhook_hides_token(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/SECRET")
    from app.config import Settings
    assert "SECRET" not in Settings().redacted_webhook()
```

- [ ] **Step 3: Run test to verify it fails** — `pytest tests/test_config.py -v` → FAIL (`No module named 'app.config'`).

- [ ] **Step 4: Implement `app/config.py`**

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_webhook_url: str
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "gemma4:12b"
    ollama_num_ctx: int = 8192
    ollama_temperature: float = 0.0
    ollama_seed: int = 42
    ollama_num_predict: int = 768
    ollama_auto_pull: bool = True
    game_types: str = "wordle,connections"
    schedule_cron: str = "15 0 * * *"
    schedule_tz: str = "America/New_York"
    manual_trigger_enabled: bool = True
    max_invalid_retries: int = 10
    wordle_hard_mode: bool = False
    nyt_user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    nyt_timeout_seconds: float = 10.0
    nyt_max_retries: int = 3
    post_on_fetch_failure: bool = False
    db_path: str = "/data/games.db"
    log_level: str = "INFO"

    @property
    def game_type_list(self) -> list[str]:
        return [g.strip() for g in self.game_types.split(",") if g.strip()]

    def redacted_webhook(self) -> str:
        # keep scheme+host, drop the token segment
        return self.discord_webhook_url.rsplit("/", 1)[0] + "/***"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Create package `__init__.py` files & `.env.example`**

Create empty `app/__init__.py`, `app/engines/__init__.py`, `app/puzzles/__init__.py`, `app/players/__init__.py`, `app/storage/__init__.py`, `app/output/__init__.py`, `app/runner/__init__.py`, `tests/__init__.py`. Write `.env.example` listing every env var from the spec §6 with placeholder values and `DISCORD_WEBHOOK_URL=` left blank.

- [ ] **Step 6: Run tests + lint** — `pip install -e ".[dev]"` then `pytest tests/test_config.py -v` → PASS; `ruff check .` → clean.

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: project scaffold and settings"`

---

## Task 2: Core domain models (`app/engines/models.py`)

**Files:**
- Create: `app/engines/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces (consumed by nearly every later task):
  - Enums: `Mark{GREEN,YELLOW,GRAY}` (str), `Level(IntEnum){YELLOW=0,GREEN=1,BLUE=2,PURPLE=3}`, `GameType{WORDLE,CONNECTIONS}` (str), `Outcome{WIN,LOSS,ERRORED}` (str), `SubmitResult{CORRECT,WIN,INCORRECT,ONE_AWAY,LOSS,ALREADY_GUESSED}` (str).
  - `WORDLE_EMOJI: dict[Mark,str]` = green 🟩 / yellow 🟨 / gray ⬜; `LEVEL_EMOJI: dict[Level,str]` = 🟨🟩🟦🟪.
  - Dataclasses (frozen where pure): `WordlePuzzle(date:str, number:int|None, solution:str, editor:str|None)`; `ConnectionsGroup(title:str, words:tuple[str,...], level:Level)`; `ConnectionsPuzzle(date:str, number:int, editor:str|None, groups:tuple[ConnectionsGroup,...])`; `MoveProblem(reason:str, feedback:str)`; `TurnRecord(turn_index:int, guess:str, feedback:str, reasoning:str, retries:int)`; `GameRecord(game_type:GameType, puzzle_date:str, puzzle_number:int|None, puzzle_id:int|None, model:str, started_at:str, finished_at:str|None, outcome:Outcome, num_guesses:int, num_mistakes:int, answer_json:str, turns:list[TurnRecord])`.

- [ ] **Step 1: Write the failing test** (`tests/test_models.py`)

```python
from app.engines.models import Level, LEVEL_EMOJI, Mark, WORDLE_EMOJI, ConnectionsGroup

def test_level_ordering_and_emoji():
    assert list(Level) == [Level.YELLOW, Level.GREEN, Level.BLUE, Level.PURPLE]
    assert LEVEL_EMOJI[Level.PURPLE] == "🟪"
    assert WORDLE_EMOJI[Mark.GREEN] == "🟩"

def test_group_word_set():
    g = ConnectionsGroup(title="X", words=("A", "B", "C", "D"), level=Level.YELLOW)
    assert set(g.words) == {"A", "B", "C", "D"}
```

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_models.py -v` → FAIL (import error).

- [ ] **Step 3: Implement `app/engines/models.py`** — define every enum, emoji map, and dataclass listed in *Interfaces*. Use `enum.Enum`/`IntEnum` and `dataclasses.dataclass`. Make `WordlePuzzle`, `ConnectionsGroup`, `ConnectionsPuzzle`, `MoveProblem` frozen. Provide `LEVEL_EMOJI = {Level.YELLOW:"🟨", Level.GREEN:"🟩", Level.BLUE:"🟦", Level.PURPLE:"🟪"}` and `WORDLE_EMOJI = {Mark.GREEN:"🟩", Mark.YELLOW:"🟨", Mark.GRAY:"⬜"}`.

- [ ] **Step 4: Run test** — `pytest tests/test_models.py -v` → PASS. `mypy app/engines/models.py` → clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: core domain models"`

---

## Task 3: Wordle engine (`app/engines/wordle_engine.py` + word list)

**Files:**
- Create: `app/engines/wordle_engine.py`, `app/wordlists/allowed_guesses.txt`
- Test: `tests/test_wordle_engine.py`

**Interfaces:**
- Consumes: `models.Mark`, `models.WordlePuzzle`, `models.MoveProblem`.
- Produces:
  - `score_guess(guess:str, solution:str) -> list[Mark]`
  - `load_allowed_guesses(path:str|None=None) -> set[str]`
  - class `WordleEngine(puzzle:WordlePuzzle, allowed:set[str], hard_mode:bool=False)` with: `render_state() -> str`, `validate_guess(guess:str) -> MoveProblem|None`, `apply_guess(guess:str) -> list[Mark]`, properties `status -> Outcome|None` (None while in progress), `attempts_used:int`, `solved:bool`, `guess_rows:list[tuple[str,list[Mark]]]`, `MAX_GUESSES=6`.

- [ ] **Step 1: Acquire the allowed-guesses list**

```bash
mkdir -p app/wordlists
curl -fsSL "https://gist.githubusercontent.com/dracos/dd0668f281e685bad51479e5acaadb93/raw/valid-wordle-words.txt" \
  -o app/wordlists/allowed_guesses.txt
wc -l app/wordlists/allowed_guesses.txt   # expect ~12972
grep -qx "token" app/wordlists/allowed_guesses.txt && echo "sanity: today's answer present"
```

- [ ] **Step 2: Write the failing tests** (`tests/test_wordle_engine.py`) — the load-bearing scoring fixtures:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail** — `pytest tests/test_wordle_engine.py -v` → FAIL (import error).

- [ ] **Step 4: Implement `app/engines/wordle_engine.py`**

```python
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
        if not self.guess_rows:
            return "No guesses yet. You have 6 attempts to find a 5-letter word."
        lines = []
        for word, marks in self.guess_rows:
            grid = "".join(WORDLE_EMOJI[m] for m in marks)
            lines.append(f"{word.upper()}  {grid}")
        lines.append(f"Attempts remaining: {MAX_GUESSES - self.attempts_used}")
        return "\n".join(lines)
```

- [ ] **Step 5: Run tests + types** — `pytest tests/test_wordle_engine.py -v` → PASS; `mypy app/engines/wordle_engine.py` → clean; `ruff check .` → clean.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: wordle engine with two-pass scoring + word list"`

---

## Task 4: Connections engine (`app/engines/connections_engine.py`)

**Files:**
- Create: `app/engines/connections_engine.py`
- Test: `tests/test_connections_engine.py`

**Interfaces:**
- Consumes: `models.{Level, LEVEL_EMOJI, ConnectionsGroup, ConnectionsPuzzle, MoveProblem, Outcome, SubmitResult}`.
- Produces: class `ConnectionsEngine(puzzle:ConnectionsPuzzle, rng:random.Random|None=None)` with: `render_state() -> str`, `validate_selection(words:list[str]) -> MoveProblem|None`, `submit(words:list[str]) -> SubmitResult`, `render_share_grid() -> str`, properties `status -> Outcome|None`, `mistakes:int`, `solved_groups:list[ConnectionsGroup]`, `remaining_words:set[str]`, `MAX_MISTAKES=4`.

- [ ] **Step 1: Write the failing tests** (`tests/test_connections_engine.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail** — `pytest tests/test_connections_engine.py -v` → FAIL.

- [ ] **Step 3: Implement `app/engines/connections_engine.py`**

```python
import random
from app.engines.models import (ConnectionsGroup, ConnectionsPuzzle, Level, LEVEL_EMOJI,
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
        words = sorted(self.remaining_words)
        solved = "\n".join(f"SOLVED [{g.title}]: {', '.join(g.words)}" for g in self.solved_groups)
        tried = "; ".join("/".join(r) for r in self.guess_rows) or "none"
        return (f"Remaining words: {', '.join(words)}\n{solved}\n"
                f"Mistakes used: {self.mistakes}/{MAX_MISTAKES}\nAlready tried: {tried}")

    def render_share_grid(self) -> str:
        return "\n".join("".join(LEVEL_EMOJI[self._word_to_group[w].level] for w in row)
                         for row in self.guess_rows)
```

- [ ] **Step 4: Run tests + types** — `pytest tests/test_connections_engine.py -v` → PASS; `mypy` clean; `ruff` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: connections engine with one-away + share grid"`

---

## Task 5: Date helper + Wordle source (`app/puzzles/dates.py`, `app/puzzles/wordle_source.py`)

**Files:**
- Create: `app/puzzles/dates.py`, `app/puzzles/wordle_source.py`, `tests/fixtures/wordle_2026-06-17.json`, `tests/fixtures/wordle_launch.json`
- Test: `tests/test_wordle_source.py`

**Interfaces:**
- Consumes: `models.WordlePuzzle`, `config.Settings`.
- Produces: `dates.today_str(tz:str) -> str`; `wordle_source.fetch_wordle(date:str, settings, client:httpx.Client|None=None) -> WordlePuzzle`; raises `wordle_source.PuzzleNotPublished` on 404.

- [ ] **Step 1: Create fixtures** — write the verified samples:
  - `tests/fixtures/wordle_2026-06-17.json`: `{"id":1735,"solution":"token","print_date":"2026-06-17","days_since_launch":1824,"editor":"Tracy Bennett"}`
  - `tests/fixtures/wordle_launch.json`: `{"id":1,"solution":"cigar","print_date":"2021-06-19"}` (no `days_since_launch`/`editor`).

- [ ] **Step 2: Write the failing test** (`tests/test_wordle_source.py`)

```python
import json, httpx, respx, pytest
from app.config import Settings
from app.puzzles.wordle_source import fetch_wordle, PuzzleNotPublished

def _settings(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    return Settings()

@respx.mock
def test_parses_modern_record(monkeypatch):
    body = json.load(open("tests/fixtures/wordle_2026-06-17.json"))
    respx.get("https://www.nytimes.com/svc/wordle/v2/2026-06-17.json").mock(return_value=httpx.Response(200, json=body))
    p = fetch_wordle("2026-06-17", _settings(monkeypatch))
    assert p.solution == "token" and p.number == 1824 and p.editor == "Tracy Bennett"

@respx.mock
def test_parses_old_record_without_optional_fields(monkeypatch):
    body = json.load(open("tests/fixtures/wordle_launch.json"))
    respx.get("https://www.nytimes.com/svc/wordle/v2/2021-06-19.json").mock(return_value=httpx.Response(200, json=body))
    p = fetch_wordle("2021-06-19", _settings(monkeypatch))
    assert p.solution == "cigar" and p.number is None and p.editor is None

@respx.mock
def test_404_raises_not_published(monkeypatch):
    respx.get("https://www.nytimes.com/svc/wordle/v2/2099-01-01.json").mock(return_value=httpx.Response(404, json={"status":"ERROR"}))
    with pytest.raises(PuzzleNotPublished):
        fetch_wordle("2099-01-01", _settings(monkeypatch))
```

- [ ] **Step 3: Run test to verify it fails** — `pytest tests/test_wordle_source.py -v` → FAIL.

- [ ] **Step 4: Implement** `app/puzzles/dates.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

def today_str(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
```

`app/puzzles/wordle_source.py`:

```python
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import Settings
from app.engines.models import WordlePuzzle

URL = "https://www.nytimes.com/svc/wordle/v2/{date}.json"


class PuzzleNotPublished(Exception):
    pass


def fetch_wordle(date: str, settings: Settings, client: httpx.Client | None = None) -> WordlePuzzle:
    owns = client is None
    client = client or httpx.Client(timeout=settings.nyt_timeout_seconds,
                                    headers={"User-Agent": settings.nyt_user_agent})
    try:
        return _fetch(date, settings, client)
    finally:
        if owns:
            client.close()


def _fetch(date: str, settings: Settings, client: httpx.Client) -> WordlePuzzle:
    @retry(stop=stop_after_attempt(settings.nyt_max_retries),
           wait=wait_exponential(multiplier=1, max=10),
           retry=retry_if_exception_type(httpx.HTTPError), reraise=True)
    def call() -> httpx.Response:
        r = client.get(URL.format(date=date))
        if r.status_code == 404:
            raise PuzzleNotPublished(date)
        r.raise_for_status()
        return r

    data = call().json()
    return WordlePuzzle(date=data["print_date"], number=data.get("days_since_launch") or data.get("id"),
                        solution=data["solution"].lower(), editor=data.get("editor"))
```

(`@retry` must not retry `PuzzleNotPublished`; it only retries `httpx.HTTPError`, so the 404 propagates immediately.)

- [ ] **Step 5: Run tests + types** — `pytest tests/test_wordle_source.py -v` → PASS; `mypy` clean.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: wordle source + date helper"`

---

## Task 6: Connections source (`app/puzzles/connections_source.py`)

**Files:**
- Create: `app/puzzles/connections_source.py`, `tests/fixtures/connections_2026-06-17.json`
- Test: `tests/test_connections_source.py`

**Interfaces:**
- Consumes: `models.{ConnectionsPuzzle, ConnectionsGroup, Level}`, `config.Settings`, `wordle_source.PuzzleNotPublished` (re-export or shared).
- Produces: `fetch_connections(date:str, settings, client:httpx.Client|None=None) -> ConnectionsPuzzle`. Difficulty `level = Level(index)` from category order. Asserts 4 groups × 4 cards and positions 0-15.

- [ ] **Step 1: Create fixture** — `tests/fixtures/connections_2026-06-17.json` = the verified payload (ALCOVE / BODILY WORDS FOR ATTITUDE / FIGURES IN GREEK MYTH / STARTING WITH SYNONYMS FOR "ILK", with the exact 16 cards + positions from the spec §7.2).

- [ ] **Step 2: Write the failing test** (`tests/test_connections_source.py`)

```python
import json, httpx, respx, pytest
from app.config import Settings
from app.engines.models import Level
from app.puzzles.connections_source import fetch_connections
from app.puzzles.wordle_source import PuzzleNotPublished

def _settings(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    return Settings()

@respx.mock
def test_levels_derived_from_order(monkeypatch):
    body = json.load(open("tests/fixtures/connections_2026-06-17.json"))
    respx.get("https://www.nytimes.com/svc/connections/v2/2026-06-17.json").mock(return_value=httpx.Response(200, json=body))
    p = fetch_connections("2026-06-17", _settings(monkeypatch))
    assert len(p.groups) == 4
    assert p.groups[0].level is Level.YELLOW and p.groups[0].title == "ALCOVE"
    assert p.groups[3].level is Level.PURPLE
    assert all(len(g.words) == 4 for g in p.groups)

@respx.mock
def test_404_raises(monkeypatch):
    respx.get("https://www.nytimes.com/svc/connections/v2/2099-01-01.json").mock(return_value=httpx.Response(404, json={"status":"ERROR"}))
    with pytest.raises(PuzzleNotPublished):
        fetch_connections("2099-01-01", _settings(monkeypatch))
```

- [ ] **Step 3: Run test to verify it fails** — `pytest tests/test_connections_source.py -v` → FAIL.

- [ ] **Step 4: Implement `app/puzzles/connections_source.py`**

```python
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import Settings
from app.engines.models import ConnectionsGroup, ConnectionsPuzzle, Level
from app.puzzles.wordle_source import PuzzleNotPublished

URL = "https://www.nytimes.com/svc/connections/v2/{date}.json"


def fetch_connections(date: str, settings: Settings, client: httpx.Client | None = None) -> ConnectionsPuzzle:
    owns = client is None
    client = client or httpx.Client(timeout=settings.nyt_timeout_seconds,
                                    headers={"User-Agent": settings.nyt_user_agent})
    try:
        return _fetch(date, settings, client)
    finally:
        if owns:
            client.close()


def _fetch(date: str, settings: Settings, client: httpx.Client) -> ConnectionsPuzzle:
    @retry(stop=stop_after_attempt(settings.nyt_max_retries),
           wait=wait_exponential(multiplier=1, max=10),
           retry=retry_if_exception_type(httpx.HTTPError), reraise=True)
    def call() -> httpx.Response:
        r = client.get(URL.format(date=date))
        if r.status_code == 404:
            raise PuzzleNotPublished(date)
        r.raise_for_status()
        return r

    data = call().json()
    cats = data["categories"]
    assert len(cats) == 4, "expected 4 categories"
    positions: set[int] = set()
    groups = []
    for idx, cat in enumerate(cats):
        cards = cat["cards"]
        assert len(cards) == 4, "expected 4 cards per category"
        positions.update(c["position"] for c in cards)
        groups.append(ConnectionsGroup(title=cat["title"],
                                       words=tuple(c["content"] for c in cards),
                                       level=Level(idx)))
    assert positions == set(range(16)), "positions must cover 0..15"
    return ConnectionsPuzzle(date=data["print_date"], number=data["id"],
                             editor=data.get("editor"), groups=tuple(groups))
```

- [ ] **Step 5: Run tests + types** — `pytest tests/test_connections_source.py -v` → PASS; `mypy` clean.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: connections source with level-by-order + invariants"`

---

## Task 7: LLM player (`app/players/llm_player.py`, prompts)

**Files:**
- Create: `app/players/llm_player.py`, `app/players/prompts/wordle.txt`, `app/players/prompts/connections.txt`
- Test: `tests/test_llm_player.py`

**Interfaces:**
- Consumes: `engines.wordle_engine.WordleEngine`, `engines.connections_engine.ConnectionsEngine` (both expose `render_state()` + a validator + an apply/submit), `models.{MoveProblem, TurnRecord}`, `config.Settings`.
- Produces:
  - Pydantic `WordleTurn{reasoning:str, guess:str}`, `ConnectionsTurn{reasoning:str, group:list[str], category_guess:str}`.
  - `class LLMPlayer(settings, client=None)` with `play_wordle(engine) -> list[TurnRecord]` and `play_connections(engine) -> list[TurnRecord]`.
  - Exception `InvalidMoveExhausted`.
  - The Ollama client is injectable (`client` arg) — anything with `.chat(model, messages, format, options) -> obj.message.content`.

- [ ] **Step 1: Write prompt templates** — `wordle.txt` and `connections.txt`: each states the rules, has `{{STATE}}` and `{{SCHEMA}}` placeholders, and instructs "Reply ONLY with JSON matching the schema."

- [ ] **Step 2: Write the failing test** (`tests/test_llm_player.py`) — fake Ollama client driving the corrective loop:

```python
import json, pytest
from app.config import Settings
from app.engines.models import WordlePuzzle
from app.engines.wordle_engine import WordleEngine
from app.players.llm_player import LLMPlayer, InvalidMoveExhausted

class FakeOllama:
    def __init__(self, replies): self._replies = list(replies); self.calls = 0
    def chat(self, **kw):
        r = self._replies[self.calls]; self.calls += 1
        return type("M", (), {"message": type("Msg", (), {"content": r})})

def _settings(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    return Settings()

def test_invalid_then_valid_does_not_consume_turn(monkeypatch):
    eng = WordleEngine(WordlePuzzle("2026-06-17", 1824, "token", None), allowed={"crane", "token"})
    fake = FakeOllama([
        json.dumps({"reasoning": "try", "guess": "zzzzz"}),   # not in dictionary -> re-prompt, no turn
        json.dumps({"reasoning": "ok", "guess": "crane"}),    # valid -> consumes turn 1
        json.dumps({"reasoning": "win", "guess": "token"}),   # valid -> win
    ])
    turns = LLMPlayer(_settings(monkeypatch), client=fake).play_wordle(eng)
    assert eng.solved
    assert [t.guess for t in turns] == ["crane", "token"]   # invalid attempt NOT recorded as a turn
    assert turns[0].retries == 1                            # one corrective re-prompt before crane

def test_backstop_raises(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    s = Settings(max_invalid_retries=2)
    eng = WordleEngine(WordlePuzzle("2026-06-17", 1824, "token", None), allowed={"crane"})
    fake = FakeOllama([json.dumps({"reasoning": "x", "guess": "zzzzz"})] * 5)
    with pytest.raises(InvalidMoveExhausted):
        LLMPlayer(s, client=fake).play_wordle(eng)
```

- [ ] **Step 3: Run test to verify it fails** — `pytest tests/test_llm_player.py -v` → FAIL.

- [ ] **Step 4: Implement `app/players/llm_player.py`**

```python
import json
from importlib.resources import files
from pydantic import BaseModel, ValidationError
from ollama import Client
from app.config import Settings
from app.engines.models import TurnRecord


class WordleTurn(BaseModel):
    reasoning: str
    guess: str


class ConnectionsTurn(BaseModel):
    reasoning: str
    group: list[str]
    category_guess: str


class InvalidMoveExhausted(Exception):
    pass


def _prompt(name: str) -> str:
    return (files("app.players.prompts") / f"{name}.txt").read_text(encoding="utf-8")


class LLMPlayer:
    def __init__(self, settings: Settings, client: object | None = None) -> None:
        self.s = settings
        self.client = client or Client(host=settings.ollama_host)

    def _opts(self) -> dict:
        return {"temperature": self.s.ollama_temperature, "seed": self.s.ollama_seed,
                "num_ctx": self.s.ollama_num_ctx, "num_predict": self.s.ollama_num_predict}

    def _ask(self, template: str, schema: type[BaseModel], state: str, correction: str | None,
             history: list[dict]) -> tuple[BaseModel, list[dict]]:
        if not history:
            content = template.replace("{{STATE}}", state).replace("{{SCHEMA}}", json.dumps(schema.model_json_schema()))
            history = [{"role": "user", "content": content}]
        if correction:
            history = history + [{"role": "user", "content": correction}]
        raw = self.client.chat(model=self.s.ollama_model, messages=history,
                               format=schema.model_json_schema(), options=self._opts()).message.content
        history = history + [{"role": "assistant", "content": raw}]
        return schema.model_validate_json(raw), history  # raises ValidationError on bad JSON

    def play_wordle(self, engine) -> list[TurnRecord]:
        template = _prompt("wordle")
        turns: list[TurnRecord] = []
        while engine.status is None:
            history: list[dict] = []
            correction: str | None = None
            retries = 0
            while True:
                if retries > self.s.max_invalid_retries:
                    raise InvalidMoveExhausted("wordle")
                try:
                    turn, history = self._ask(template, WordleTurn, engine.render_state(), correction, history)
                except (ValidationError, json.JSONDecodeError) as e:
                    correction, retries = f"Invalid JSON for the schema: {e}. Reply ONLY with JSON.", retries + 1
                    continue
                problem = engine.validate_guess(turn.guess)
                if problem is None:
                    break
                correction, retries = problem.feedback, retries + 1
            marks = engine.apply_guess(turn.guess)
            turns.append(TurnRecord(len(turns), turn.guess.lower(),
                                    "".join(m.value for m in marks), turn.reasoning, retries))
        return turns

    def play_connections(self, engine) -> list[TurnRecord]:
        template = _prompt("connections")
        turns: list[TurnRecord] = []
        while engine.status is None:
            history: list[dict] = []
            correction: str | None = None
            retries = 0
            while True:
                if retries > self.s.max_invalid_retries:
                    raise InvalidMoveExhausted("connections")
                try:
                    turn, history = self._ask(template, ConnectionsTurn, engine.render_state(), correction, history)
                except (ValidationError, json.JSONDecodeError) as e:
                    correction, retries = f"Invalid JSON for the schema: {e}. Reply ONLY with JSON.", retries + 1
                    continue
                problem = engine.validate_selection(turn.group)
                if problem is None:
                    break
                correction, retries = problem.feedback, retries + 1
            result = engine.submit(turn.group)
            turns.append(TurnRecord(len(turns), "/".join(turn.group), result.value, turn.reasoning, retries))
        return turns
```

- [ ] **Step 5: Run tests + types** — `pytest tests/test_llm_player.py -v` → PASS; `mypy` clean; `ruff` clean.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: llm player with structured output + corrective retry loop"`

---

## Task 8: Storage (`app/storage/db.py`, `app/storage/stats.py`)

**Files:**
- Create: `app/storage/db.py`, `app/storage/stats.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `models.{GameRecord, TurnRecord, GameType, Outcome}`.
- Produces: `init_db(path:str) -> sqlite3.Connection`; class `GameRepository(conn)` with `exists(game_type:str, date:str, model:str) -> bool`, `delete(game_type, date, model) -> None`, `save(record:GameRecord) -> int`; `stats.win_rate_by_model(conn) -> list[dict]`. Schema per spec §11 (the two-table DDL + `UNIQUE(game_type, puzzle_date, model)`).

- [ ] **Step 1: Write the failing test** (`tests/test_storage.py`)

```python
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
```

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_storage.py -v` → FAIL.

- [ ] **Step 3: Implement `app/storage/db.py`** — open SQLite with `PRAGMA foreign_keys=ON`, create the two tables from spec §11 if absent, implement `exists` (SELECT 1), `delete` (DELETE by the unique triple; cascade removes turns), `save` (INSERT game, then INSERT each turn with the returned `game_id`; serialize lists with `json`). Implement `app/storage/stats.py::win_rate_by_model` as a `GROUP BY model` query returning dicts.

- [ ] **Step 4: Run tests + types** — `pytest tests/test_storage.py -v` → PASS; `mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: sqlite storage with idempotency + stats"`

---

## Task 9: Discord webhook poster (`app/output/discord_webhook.py`)

**Files:**
- Create: `app/output/discord_webhook.py`
- Test: `tests/test_discord_webhook.py`

**Interfaces:**
- Consumes: `config.Settings`.
- Produces: `build_wordle_embed(number, marks_rows:list[str], solution:str, model:str, won:bool) -> dict`; `build_connections_embed(number, grid:str, groups_text:str, model:str, mistakes:int, won:bool) -> dict`; `post_embed(embed:dict, settings, client:httpx.Client|None=None) -> None` (sets `allowed_mentions={"parse":[]}`, honors 429 `Retry-After`). Colors are decimal ints; the answer line is wrapped in `||...||` (not inside a code block).

- [ ] **Step 1: Write the failing test** (`tests/test_discord_webhook.py`)

```python
import httpx, respx
from app.config import Settings
from app.output.discord_webhook import build_wordle_embed, post_embed

def _s(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
    return Settings()

def test_embed_spoilers_answer_and_decimal_color(monkeypatch):
    e = build_wordle_embed(1824, ["🟩🟩🟩🟩🟩"], "token", "gemma4:12b", won=True)
    assert isinstance(e["color"], int)
    assert "||" in e["description"] and "TOKEN" in e["description"].upper()
    assert "```" not in e["description"]   # spoiler must not be in a code block

@respx.mock
def test_post_sets_no_mention_and_posts(monkeypatch):
    route = respx.post("https://discord.com/api/webhooks/1/tok").mock(return_value=httpx.Response(204))
    post_embed({"title": "x"}, _s(monkeypatch))
    body = route.calls[0].request.content
    assert b'"parse": []' in body or b'"parse":[]' in body
```

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_discord_webhook.py -v` → FAIL.

- [ ] **Step 3: Implement `app/output/discord_webhook.py`** — color constants as decimals (green `5763719`, red `15548997`); `build_*_embed` returns `{title, description, color, footer}` with the grid + `\n\n||answer||`; `post_embed` POSTs `{"embeds":[embed], "allowed_mentions":{"parse":[]}}`; on HTTP 429, sleep `float(resp.headers.get("Retry-After", 1))` and retry up to 3×. Never log `settings.discord_webhook_url` (use `settings.redacted_webhook()`).

- [ ] **Step 4: Run tests + types** — `pytest tests/test_discord_webhook.py -v` → PASS; `mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: discord webhook poster with spoiler embeds + backoff"`

---

## Task 10: Game runner (`app/runner/game_runner.py`)

**Files:**
- Create: `app/runner/game_runner.py`
- Test: `tests/test_game_runner.py`

**Interfaces:**
- Consumes: everything from T3–T9 (`fetch_wordle`/`fetch_connections`, engines, `LLMPlayer`, `GameRepository`, `discord_webhook`, `dates.today_str`).
- Produces: `run_wordle(date:str, settings, *, player, repo, http=None, force=False) -> GameRecord|None`; `run_connections(...) -> GameRecord|None` (returns `None` when skipped by idempotency or `PuzzleNotPublished`). Builds `GameRecord`, persists, posts the embed.

- [ ] **Step 1: Write the failing test** (`tests/test_game_runner.py`) — wire fakes: a fake `player` whose `play_wordle` drives a real `WordleEngine` to a win, an in-memory `GameRepository`, and `respx` for the NYT GET + Discord POST. Assert a `GameRecord` with `outcome == WIN` is saved and the Discord route was called once. Add a second test asserting idempotency: a pre-existing record → `run_wordle` returns `None` and posts nothing (unless `force=True`).

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_game_runner.py -v` → FAIL.

- [ ] **Step 3: Implement `app/runner/game_runner.py`** — for each game: idempotency check (`repo.exists`; if `force`, `repo.delete` first); `fetch_*` (catch `PuzzleNotPublished` → log + return `None`); build engine (Wordle uses `load_allowed_guesses()`; Connections uses an injected/fresh `random.Random`); `player.play_*(engine)` → list[TurnRecord]; assemble `GameRecord` (outcome from `engine.status`, `num_guesses`, `num_mistakes` per spec §11 semantics, `answer_json`); `repo.save`; build + `post_embed`. Catch `InvalidMoveExhausted` → record `Outcome.ERRORED`, persist, optionally post per `post_on_fetch_failure`.

- [ ] **Step 4: Run tests + types** — `pytest tests/test_game_runner.py -v` → PASS; `mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: game runner orchestration"`

---

## Task 11: Scheduler + FastAPI app (`app/runner/scheduler.py`, `app/runner/app.py`)

**Files:**
- Create: `app/runner/scheduler.py`, `app/runner/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `game_runner`, `config.Settings`, `dates.today_str`, `storage.init_db`/`GameRepository`, `LLMPlayer`; `ollama` for the startup auto-pull.
- Produces: `build_app(settings) -> FastAPI` exposing `GET /healthz` (liveness + ollama reachability + model + next run) and `POST /play?game=wordle|connections|both&force=bool`; `run_cycle(settings, game_types:list[str], force=False) -> list[GameRecord]`; `ensure_model(settings) -> None` (pull if missing when `ollama_auto_pull`). The APScheduler `BackgroundScheduler` registers `run_cycle` on the `schedule_cron` trigger at startup.

- [ ] **Step 1: Write the failing test** (`tests/test_app.py`) — use FastAPI `TestClient`; monkeypatch `run_cycle` to a stub; assert `GET /healthz` returns 200 with the model name, and `POST /play?game=wordle` invokes the stub. Disable the scheduler in tests (env/flag) so no real cron fires.

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_app.py -v` → FAIL.

- [ ] **Step 3: Implement `scheduler.py`** (`make_scheduler(settings, job)` returning a configured `BackgroundScheduler` with a `CronTrigger.from_crontab(settings.schedule_cron, timezone=settings.schedule_tz)`) and `app.py` (`build_app`: on `startup` call `ensure_model` + start scheduler unless `TESTING`; define `/healthz` and `/play`; `run_cycle` opens the DB, builds `LLMPlayer`, loops `settings.game_type_list` calling the right `run_*`). `ensure_model` uses `ollama.Client(host=...).list()` then `.pull(model)` if absent.

- [ ] **Step 4: Run tests + types** — `pytest tests/test_app.py -v` → PASS; `mypy app` → clean; `ruff check .` → clean; full `pytest` → green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: fastapi app + apscheduler + manual trigger"`

---

## Task 12: Dockerization (`Dockerfile`, compose, entrypoint)

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `docker-compose.gpu.yml`, `entrypoint.sh`
- Test: manual `docker compose config` + smoke.

**Interfaces:** none (deploys the whole app).

- [ ] **Step 1: Write `Dockerfile`** — `FROM python:3.12-slim`; create a non-root user; `COPY pyproject.toml`, `pip install .`; `COPY app ./app`; `EXPOSE 8080`; `CMD ["uvicorn", "app.runner.app:app", "--host", "0.0.0.0", "--port", "8080"]` (expose `app = build_app(get_settings())` at module scope in `app/runner/app.py`).

- [ ] **Step 2: Write `docker-compose.yml`** — `ollama` service (`image: ollama/ollama`, volume `ollama_models:/root/.ollama`, healthcheck `CMD curl -f http://localhost:11434/api/tags`); `bot` service (build `.`, `depends_on: { ollama: { condition: service_healthy } }`, `env_file: .env`, volume `bot_data:/data`, `environment: OLLAMA_HOST=http://ollama:11434`). Named volumes `ollama_models`, `bot_data`.

- [ ] **Step 3: Write `docker-compose.gpu.yml`** — override adding the NVIDIA `deploy.resources.reservations.devices` block (spec §4.2) to `ollama`.

- [ ] **Step 4: Validate** — `docker compose config` → valid; `docker compose -f docker-compose.yml -f docker-compose.gpu.yml config` → valid. (Optional live smoke: `docker compose up -d`, wait for `ollama` healthy + model pull, `curl localhost:8080/healthz` → 200, `curl -X POST 'localhost:8080/play?game=wordle&force=true'` → a Discord post appears.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: dockerization with CPU/GPU compose split"`

---

## Self-Review (performed)

- **Spec coverage:** every spec section maps to a task — sources §7→T5/T6, engines §8→T3/T4, LLM contract §9→T7, runner §10→T10, persistence §11→T8, Discord §12→T9, scheduling §13→T11, config §6→T1, Docker §4→T12, models→T2. Error-handling §14 and security §15 are folded into the owning tasks (404/backoff in T5/T6/T9, redaction in T1/T9, `errored` outcome in T7/T10). Testing §16 fixtures appear in T3/T5/T6/T7.
- **Placeholder scan:** no "TBD"/"handle edge cases" left; the few prose-only steps (T8 S3, T9 S3, T10 S3, T11 S3) specify exact functions, queries, and behaviors rather than vague directions, and all surrounding tasks carry complete code + tests.
- **Type consistency:** names are stable across tasks — `validate_guess`/`apply_guess` (Wordle), `validate_selection`/`submit` (Connections), `render_state`/`render_share_grid`, `fetch_wordle`/`fetch_connections`, `PuzzleNotPublished`, `LLMPlayer.play_wordle/play_connections`, `GameRepository.exists/delete/save`, `TurnRecord(turn_index, guess, feedback, reasoning, retries)` are used identically by their consumers.

---

## Workflow Execution

This plan is executable by a Claude Code **Workflow**. The script below maps the DAG to phases, runs Phase B in parallel, and **serializes all git commits** through dedicated commit agents (parallel build agents never touch git). Each build agent reads its task from this plan file and returns a structured manifest.

**Save the script and run it with:**
`Workflow({ scriptPath: "<path>", args: { planPath: "docs/superpowers/plans/2026-06-17-wordle-connections-discord-bot.md", repo: "/Users/aworland/projects/wordle-connections-bot" } })`

```javascript
export const meta = {
  name: 'build-wordle-connections-bot',
  description: 'Implement the Wordle/Connections LLM Discord bot from its plan: foundation → parallel modules → integration → docker, with serialized commits',
  phases: [
    { title: 'A-foundation', detail: 'scaffold+config (T1) then models (T2), committed serially' },
    { title: 'B-modules', detail: 'T3,T4,T5,T6,T8,T9 built in parallel (no git), then a serial commit pass' },
    { title: 'B2-player', detail: 'T7 llm-player (needs T3,T4)' },
    { title: 'C-integration', detail: 'T10 runner then T11 app' },
    { title: 'D-docker', detail: 'T12 containerization + compose validate' },
  ],
}

const repo = args.repo
const plan = args.planPath
const MANIFEST = {
  type: 'object', additionalProperties: false,
  required: ['task', 'files_written', 'tests_command', 'tests_passed', 'notes'],
  properties: {
    task: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    tests_command: { type: 'string' },
    tests_passed: { type: 'boolean' },
    notes: { type: 'string' },
  },
}
const COMMIT = {
  type: 'object', additionalProperties: false,
  required: ['committed', 'sha', 'suite_passed', 'notes'],
  properties: {
    committed: { type: 'boolean' }, sha: { type: 'string' },
    suite_passed: { type: 'boolean' }, notes: { type: 'string' },
  },
}

const build = (task, extra = '') =>
  `Working dir: ${repo}. Read the plan at ${plan} and implement ${task} EXACTLY, following its TDD steps `
  + `(write failing test → confirm it fails → implement → confirm pass). Run that task's pytest command and ensure it is green, `
  + `plus \`ruff check .\` and \`mypy app\`. ${extra} `
  + `CRITICAL: do NOT run any git command — another agent commits. Only create/edit files and run tests. Return the manifest.`

const commit = (label, tasks, msg) =>
  `Working dir: ${repo}. Run the FULL suite \`pytest -q\` and \`ruff check .\`. If green, \`git add -A\` and commit with message "${msg}". `
  + `This commits the work for ${tasks}. Return {committed, sha (from \`git rev-parse HEAD\`), suite_passed, notes}. If the suite fails, set committed=false and explain.`

// ---- Phase A: foundation (serial) ----
phase('A-foundation')
await agent(build('Task 1 (project scaffold & config)'), { label: 'T1-scaffold', phase: 'A-foundation', schema: MANIFEST })
await agent(commit('A1', 'Task 1', 'feat: project scaffold and settings'), { label: 'commit-T1', phase: 'A-foundation', schema: COMMIT })
await agent(build('Task 2 (core domain models)'), { label: 'T2-models', phase: 'A-foundation', schema: MANIFEST })
await agent(commit('A2', 'Task 2', 'feat: core domain models'), { label: 'commit-T2', phase: 'A-foundation', schema: COMMIT })

// ---- Phase B: independent modules in parallel (NO git), then serial commits in dep order ----
phase('B-modules')
const bTasks = [
  { t: 'Task 3 (wordle engine + word list)', label: 'T3-wordle-engine', msg: 'feat: wordle engine with two-pass scoring + word list' },
  { t: 'Task 4 (connections engine)',        label: 'T4-conn-engine',   msg: 'feat: connections engine with one-away + share grid' },
  { t: 'Task 5 (date helper + wordle source)', label: 'T5-wordle-source', msg: 'feat: wordle source + date helper' },
  { t: 'Task 6 (connections source)',        label: 'T6-conn-source',   msg: 'feat: connections source with level-by-order' },
  { t: 'Task 8 (sqlite storage)',            label: 'T8-storage',       msg: 'feat: sqlite storage with idempotency + stats' },
  { t: 'Task 9 (discord webhook poster)',    label: 'T9-discord',       msg: 'feat: discord webhook poster' },
]
const built = await parallel(bTasks.map((b) => () =>
  agent(build(b.t), { label: b.label, phase: 'B-modules', schema: MANIFEST })))
const failed = bTasks.filter((_, i) => !built[i] || !built[i].tests_passed)
if (failed.length) log(`⚠ Phase B build issues: ${failed.map((f) => f.label).join(', ')} — commit agent will re-run suite`)
for (const b of bTasks) {   // serial commits, dependency order
  await agent(commit(b.label, b.t, b.msg), { label: `commit-${b.label}`, phase: 'B-modules', schema: COMMIT })
}

// ---- Phase B2: player (depends on T3,T4) ----
phase('B2-player')
await agent(build('Task 7 (llm player + prompts)'), { label: 'T7-llm-player', phase: 'B2-player', schema: MANIFEST })
await agent(commit('B2', 'Task 7', 'feat: llm player with structured output + corrective retry loop'),
            { label: 'commit-T7', phase: 'B2-player', schema: COMMIT })

// ---- Phase C: integration (serial) ----
phase('C-integration')
await agent(build('Task 10 (game runner)'), { label: 'T10-runner', phase: 'C-integration', schema: MANIFEST })
await agent(commit('C10', 'Task 10', 'feat: game runner orchestration'), { label: 'commit-T10', phase: 'C-integration', schema: COMMIT })
await agent(build('Task 11 (fastapi app + scheduler)'), { label: 'T11-app', phase: 'C-integration', schema: MANIFEST })
await agent(commit('C11', 'Task 11', 'feat: fastapi app + apscheduler + manual trigger'), { label: 'commit-T11', phase: 'C-integration', schema: COMMIT })

// ---- Phase D: docker ----
phase('D-docker')
await agent(build('Task 12 (dockerization)', 'For Step 4 run `docker compose config` (and the gpu override config) instead of live `up`.'),
            { label: 'T12-docker', phase: 'D-docker', schema: MANIFEST })
await agent(commit('D12', 'Task 12', 'feat: dockerization with CPU/GPU compose split'), { label: 'commit-T12', phase: 'D-docker', schema: COMMIT })

return { built, message: 'Plan executed: foundation → modules → player → integration → docker.' }
```

**Notes on the workflow design:**
- **Commits are serialized** (one commit agent at a time) so they never race on `index.lock`; build agents are explicitly forbidden from running git.
- **Phase B build agents touch disjoint files** (`engines/wordle_engine.py`+`wordlists/`, `engines/connections_engine.py`, `puzzles/` ×2, `storage/`, `output/`) so parallel writes don't collide. They share only read-only foundation files from Phase A.
- **Each agent re-derives its code from this plan**, which contains complete code — so an agent with zero prior context can implement its task.
- **A commit agent re-runs the full suite before committing**, catching any cross-module integration break a single-task agent might miss.
- To **resume** after a failure, re-invoke with `resumeFromRunId` — completed agents return cached results and only the failed/after tasks re-run.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-17-wordle-connections-discord-bot.md`. Three execution options:

1. **Claude Code Workflow (matches your request)** — run the script above; it builds the whole project autonomously (parallel modules, serialized commits) and reports a manifest per task.
2. **Subagent-Driven (superpowers default)** — a fresh subagent per task with a two-stage review between tasks; slower but tighter human oversight.
3. **Inline Execution** — execute tasks in this session via `executing-plans`, batched with checkpoints.
