import json
import re
from importlib.resources import files
from typing import TypeVar

from pydantic import BaseModel
from pydantic import ValidationError
from pydantic import field_validator

from app.config import Settings
from app.engines.connections_engine import ConnectionsEngine
from app.engines.models import TurnRecord
from app.engines.wordle_engine import WordleEngine
from app.players.backend import LLMBackend
from app.players.backend import create_backend

_TurnT = TypeVar("_TurnT", bound=BaseModel)


class WordleTurn(BaseModel):
    reasoning: str
    guess: str


class ConnectionsTurn(BaseModel):
    reasoning: str
    group: list[str]
    category_guess: str

    @field_validator("group", mode="before")
    @classmethod
    def _unwrap_items(cls, v: object) -> object:
        # Some models echo the JSON Schema structure and return {"items": [...]}
        # instead of a plain list. Unwrap it transparently.
        if isinstance(v, dict):
            if "items" in v and isinstance(v["items"], list):
                return v["items"]
        return v


class InvalidMoveExhausted(Exception):
    pass


def _prompt(name: str) -> str:
    return (files("app.players.prompts") / f"{name}.txt").read_text(encoding="utf-8")


def _unwrap_schema_echo(text: str) -> str:
    """Unwrap {"properties": {...}, "required": [...]} schema-echo responses.

    Some models (e.g. gemma4 with format=json) echo the JSON Schema structure
    instead of producing a flat instance. If the parsed JSON has a "properties"
    key whose value is a dict, promote its contents to the top level.
    """
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(obj, dict) and "properties" in obj and isinstance(obj["properties"], dict):
        return json.dumps(obj["properties"])
    return text


def _strip_code_fence(text: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` wrappers that some models add around JSON."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _sanitize_json_strings(text: str) -> str:
    """Escape literal newlines/control chars inside JSON string values.

    Some models embed raw newlines in multi-line reasoning strings, which is
    invalid JSON. This walks the text character-by-character, tracking string
    boundaries, and escapes any bare control chars found inside string values.
    """
    out: list[str] = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            out.append(ch)
            escape_next = False
        elif ch == "\\":
            out.append(ch)
            escape_next = True
        elif ch == '"':
            out.append(ch)
            in_string = not in_string
        elif in_string and ch == "\n":
            out.append("\\n")
        elif in_string and ch == "\r":
            out.append("\\r")
        elif in_string and ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


class LLMPlayer:
    def __init__(self, settings: Settings, backend: LLMBackend | None = None) -> None:
        self.s = settings
        self._backend = backend if backend is not None else create_backend(settings)

    def _ask(
        self,
        template: str,
        schema: type[_TurnT],
        state: str,
        correction: str | None,
        history: list[dict[str, str]],
        temperature: float | None = None,
    ) -> tuple[_TurnT, list[dict[str, str]]]:
        schema_hint = json.dumps(schema.model_json_schema())
        if not history:
            # First turn: full rules + initial state.
            content = template.replace("{{STATE}}", state).replace("{{SCHEMA}}", schema_hint)
            history = [{"role": "user", "content": content}]
        elif correction:
            # Invalid move: prefix with [VALIDATOR] so the model distinguishes automated
            # rejection from human feedback. Must stay role:user — mid-conversation
            # system messages are silently dropped by Ollama. Do NOT re-include the
            # schema hint — it causes the model to echo the schema structure instead of
            # a flat instance.
            history = history + [{"role": "user", "content": f"[VALIDATOR] {correction}\n\nReply ONLY with JSON matching the original schema."}]
        else:
            # New turn after a valid guess: show updated state; rules already in context.
            history = history + [
                {
                    "role": "user",
                    "content": (
                        f"Updated game state:\n{state}\n\n"
                        f"Reply ONLY with JSON — no prose, no markdown, no code fences.\n"
                        f"Schema: {schema_hint}"
                    ),
                }
            ]
        raw = self._backend.complete(history, temperature=temperature)  # format="json" default
        clean = _unwrap_schema_echo(_sanitize_json_strings(_strip_code_fence(raw)))
        history = history + [{"role": "assistant", "content": raw}]
        return schema.model_validate_json(clean), history

    def _ask_free(self, history: list[dict[str, str]], message: str) -> str:
        """Send a free-text follow-up using the existing conversation history."""
        history = history + [{"role": "user", "content": message}]
        return self._backend.complete(history, format=None)

    def play_wordle(self, engine: WordleEngine) -> tuple[list[TurnRecord], str | None]:
        template = _prompt("wordle")
        turns: list[TurnRecord] = []
        history: list[dict[str, str]] = []
        while engine.status is None:
            # Phase 1 — structural retries for JSON/schema failures only.
            correction: str | None = None
            schema_retries = 0
            while True:
                if schema_retries > self.s.max_invalid_retries:
                    raise InvalidMoveExhausted("wordle")
                try:
                    turn, history = self._ask(
                        template, WordleTurn, engine.render_state(), correction, history
                    )
                    break
                except (ValidationError, json.JSONDecodeError) as e:
                    correction = f"Invalid JSON for the schema: {e}. Reply ONLY with JSON."
                    schema_retries += 1

            # Phase 2 — up to 3 move corrections (non-word, hard-mode violation).
            assert isinstance(turn, WordleTurn)
            selection_retries = 0
            while True:
                problem = engine.validate_guess(turn.guess)
                if problem is None:
                    break
                if selection_retries >= 3:
                    raise InvalidMoveExhausted("wordle")
                correction = f"[VALIDATOR] {problem.feedback}"
                try:
                    turn, history = self._ask(
                        template, WordleTurn, engine.render_state(), correction, history,
                        temperature=self.s.ollama_retry_temperature,
                    )
                except (ValidationError, json.JSONDecodeError):
                    raise InvalidMoveExhausted("wordle")
                selection_retries += 1

            marks = engine.apply_guess(turn.guess)
            turns.append(
                TurnRecord(
                    len(turns),
                    turn.guess.lower(),
                    "".join(m.value for m in marks),
                    turn.reasoning,
                    schema_retries,
                )
            )
        postmortem: str | None = None
        if engine.status is not None and engine.status.value == "loss":
            postmortem = self._ask_free(
                history,
                f"You lost. The answer was {engine.solution.upper()}. "
                f"Looking back at your guesses and the feedback you received, "
                f"explain why you did not arrive at this word.",
            )
        return turns, postmortem

    def play_connections(self, engine: ConnectionsEngine) -> list[TurnRecord]:
        template = _prompt("connections")
        turns: list[TurnRecord] = []
        history: list[dict[str, str]] = []
        while engine.status is None:
            # Phase 1 — structural retries: only for JSON/schema failures.
            # The model failed to produce parseable output; retry with a correction.
            correction: str | None = None
            schema_retries = 0
            while True:
                if schema_retries > self.s.max_invalid_retries:
                    raise InvalidMoveExhausted("connections")
                try:
                    turn, history = self._ask(
                        template, ConnectionsTurn, engine.render_state(), correction, history
                    )
                    break
                except (ValidationError, json.JSONDecodeError) as e:
                    correction = f"Invalid JSON for the schema: {e}. Reply ONLY with JSON."
                    schema_retries += 1

            # Phase 2 — up to 3 move corrections (structural failures only).
            # For "repeat" failures, name the exact words to avoid and list remaining
            # words so the model can pick a genuinely different group. If the model
            # remains cornered (can only repeat / hallucinate), forfeit → LOSS, since
            # real moves were already made.
            assert isinstance(turn, ConnectionsTurn)
            selection_retries = 0
            cornered = False
            while engine.validate_selection(turn.group) is not None:
                if selection_retries >= 3:
                    cornered = True
                    break
                problem = engine.validate_selection(turn.group)
                assert problem is not None
                if problem.reason == "repeat":
                    remaining = engine.remaining_words
                    correction = (
                        f"[VALIDATOR] You already submitted exactly those 4 words. "
                        f"DO NOT repeat them. The remaining words are: "
                        f"{', '.join(sorted(remaining))}. "
                        f"Choose a completely different group of 4."
                    )
                else:
                    correction = f"[VALIDATOR] {problem.feedback}"
                try:
                    turn, history = self._ask(
                        template, ConnectionsTurn, engine.render_state(), correction, history,
                        temperature=self.s.ollama_retry_temperature,
                    )
                except (ValidationError, json.JSONDecodeError):
                    cornered = True
                    break
                selection_retries += 1

            if cornered:
                engine.forfeit()
                break

            result = engine.submit(turn.group)
            turns.append(
                TurnRecord(
                    len(turns),
                    "/".join(turn.group),
                    result.value,
                    turn.reasoning,
                    schema_retries,
                )
            )
        return turns
