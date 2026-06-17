# CLAUDE.md — Wordle & Connections LLM Bot

Guidance for Claude Code when working in this repo. Read this before making changes.

## What this is

A Dockerized Python service that **auto-plays the day's real NYT Wordle and Connections puzzles
with a local Ollama LLM** and posts the results to a Discord channel via an **output-only webhook**.
It fetches the genuine daily puzzles from NYT's unofficial JSON endpoints, models each game in a pure
engine, shows the live state to the LLM, scores its guesses exactly like the real game, and loops
until win / loss / turn cap. Every game is persisted to SQLite tagged by model for win-rate comparison.

- **Design spec:** `docs/superpowers/specs/2026-06-17-wordle-connections-discord-bot-design.md`
- **Implementation plan:** `docs/superpowers/plans/2026-06-17-wordle-connections-discord-bot.md`

Read those two for the full rationale; this file is the working summary.

## Commands

This Mac is PEP 668 (externally-managed) — **always use the project venv**, never system pip.

```bash
# one-time setup
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# the quality gates (all must pass before committing)
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy app

# run the whole stack (macOS / CPU)
docker compose up -d
# Linux + NVIDIA (GPU): add the override
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d

# manual trigger (loopback only; add the header if PLAY_AUTH_TOKEN is set)
curl -X POST 'http://127.0.0.1:8080/play?game=both&force=true' -H 'X-Play-Token: <token>'
curl http://127.0.0.1:8080/healthz
```

> `docker compose config` fails without a `.env` because `bot` uses `env_file: .env` (git-ignored).
> To validate compose: `cp .env.example .env`, append a dummy `DISCORD_WEBHOOK_URL=...`, validate,
> then `rm .env`.

## Architecture

One long-running `bot` container (FastAPI + APScheduler) talks to a separate `ollama` container,
NYT, and the Discord webhook. There is **no inbound Discord traffic** — Discord is output-only.

```
scheduler/POST /play → game_runner → NYT source → engine ⇄ llm_player → storage(SQLite) → discord_webhook
```

### Module map (`app/`)
| Path | Responsibility |
|------|----------------|
| `config.py` | `Settings` (pydantic-settings), `get_settings()`, `redacted_webhook()` |
| `engines/models.py` | All domain types/enums (pure): `Mark`, `Level`, `GameType`, `Outcome`, `SubmitResult`, puzzles, `GameRecord`, `TurnRecord` |
| `engines/wordle_engine.py` | `score_guess()` (two-pass), `load_allowed_guesses()`, `WordleEngine` |
| `engines/connections_engine.py` | `ConnectionsEngine` (overlap/one-away/lock/share-grid) |
| `puzzles/dates.py` | `today_str(tz)` (America/New_York) |
| `puzzles/wordle_source.py` | `fetch_wordle()`, `PuzzleNotPublished` |
| `puzzles/connections_source.py` | `fetch_connections()` |
| `players/llm_player.py` | `LLMPlayer.play_wordle/play_connections`, `WordleTurn`/`ConnectionsTurn`, `InvalidMoveExhausted` |
| `players/prompts/*.txt` | Prompt templates (`{{STATE}}` + `{{SCHEMA}}` placeholders) |
| `storage/db.py` | `init_db()`, `GameRepository(exists/delete/save)` |
| `storage/stats.py` | `win_rate_by_model()` |
| `output/discord_webhook.py` | `build_wordle_embed`/`build_connections_embed`/`post_embed` |
| `runner/game_runner.py` | `run_wordle`/`run_connections` (fetch→engine→player→persist→post) |
| `runner/scheduler.py` | `make_scheduler()` (APScheduler cron) |
| `runner/app.py` | FastAPI `build_app()`, `run_cycle()`, `ensure_model()`, `/healthz`, `/play`, module-level `app` |
| `wordlists/allowed_guesses.txt` | ~14.8k valid 5-letter words for guess validation |

Engines are **pure** (no network/clock/RNG except an injected `random.Random`) so they test deterministically.

## Non-negotiable constraints (these are correctness/security load-bearing)

1. **Wordle scoring is two-pass multiset.** Greens consume letter counts *before* yellows are assigned
   (`engines/wordle_engine.py:score_guess`). Never "yellow if ch in solution" — it over-marks dupes.
   Regression fixtures: `ALLEY/LEAFY`, `EERIE/ELDER`, `SPEED/ERASE`.
2. **Connections difficulty has NO data field.** Color = category array index
   (`categories[0]`→yellow … `[3]`→purple). It's derived in `connections_source.py`; don't expect a `level` key.
3. **Ollama `format=` schemas must not use `pattern`/regex** (Ollama 500s). Validate length/charset/membership in Python.
4. **`OLLAMA_HOST` is `http://ollama:11434`** (compose DNS), never `localhost`.
5. **Invalid LLM guesses → corrective re-prompt, NO turn consumed.** `MAX_INVALID_RETRIES` is an
   infinite-loop backstop only; exhaustion ⇒ `Outcome.ERRORED` (distinct from `LOSS`).
6. **`DISCORD_WEBHOOK_URL` is a secret** — never log it (use `redacted_webhook()`), never commit. `.env` is git-ignored.
7. **`/play` is gated + authenticated.** Returns 403 when `MANUAL_TRIGGER_ENABLED=false`; when
   `PLAY_AUTH_TOKEN` is set, requires header `X-Play-Token` compared with `hmac.compare_digest`
   (constant-time). The compose host port binds `127.0.0.1` only. Keep all of this when editing `app.py`.
8. **Discord:** embed `color` is a decimal int; spoilers `||...||` do NOT work inside code blocks; set
   `allowed_mentions={"parse": []}`.
9. **Dates in `America/New_York`**; send a browser `User-Agent` to NYT (Connections is behind DataDome).
10. **Idempotency:** SQLite `UNIQUE(game_type, puzzle_date, model)`; same day+model is a no-op unless `force=true`.

## Conventions

- **TDD:** failing test → confirm fail → implement → confirm pass → commit. Every change keeps
  `pytest`/`ruff`/`mypy` green before commit.
- **Imports:** one per line (ruff E401); no unused imports (F401).
- **Tests** live in `tests/`, use the venv, real captured NYT JSON in `tests/fixtures/`, and fakes/mocks
  for Ollama (`FakeOllama`), HTTP (`respx`), and SQLite (`tmp_path`). No network in tests.
- **Config** is env-only via `pydantic-settings`; document new vars in `.env.example`.

## Git / pushing (account gotcha)

The remote is **`AlexWorland/wordle-connections-bot`** (personal, private). The shell's `GITHUB_TOKEN`
env var is pinned to a *work* account and overrides gh, so a plain `git push` will 403 here. Push as:

```bash
env -u GITHUB_TOKEN git push        # or: unset GITHUB_TOKEN
```

End commit messages with the standard co-author trailer.

## Not yet done

The full loop has only been exercised with fakes/mocks — no real `docker compose up` smoke test has run
(needs Ollama + a pulled `gemma4:12b` + a real webhook). Prompt templates (`players/prompts/*.txt`) are
the most likely thing to need tuning once you watch the model actually play.
