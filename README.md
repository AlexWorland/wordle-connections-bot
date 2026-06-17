# Wordle & Connections LLM Bot

A self-contained, Dockerized service that autonomously plays the day's real **NYT Wordle** and
**Connections** puzzles using a **local Ollama LLM**, and posts the results to Discord via an
output-only webhook.

- The backend fetches today's genuine puzzles from NYT's unofficial JSON endpoints, models each game
  faithfully, shows the live game state to the LLM, scores its guesses exactly like the real game, and
  loops until win / loss / turn cap.
- Runs entirely in Docker (backend **and** Ollama). CPU on macOS; GPU on Linux/NVIDIA via a compose
  override. Default model `gemma4:12b` (configurable).
- Plays daily on a schedule (and on demand), persists every game to SQLite tagged by model for
  win-rate comparison, and posts a final emoji share-grid with the answer hidden behind a spoiler tag.

## Status

Pre-implementation. The full design specification lives at:

[`docs/superpowers/specs/2026-06-17-wordle-connections-discord-bot-design.md`](docs/superpowers/specs/2026-06-17-wordle-connections-discord-bot-design.md)

The implementation plan is generated next (superpowers `writing-plans`).
