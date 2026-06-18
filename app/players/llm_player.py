import json
import re
from importlib.resources import files
from typing import Any
from typing import Protocol

from ollama import Client
from pydantic import BaseModel
from pydantic import ValidationError
from pydantic import field_validator

from app.config import Settings
from app.engines.connections_engine import ConnectionsEngine
from app.engines.models import TurnRecord
from app.engines.wordle_engine import WordleEngine


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


class _ChatMessage(Protocol):
    content: str


class _ChatResponse(Protocol):
    message: _ChatMessage


class _ChatClient(Protocol):
    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        format: dict[str, Any],
        options: dict[str, Any],
    ) -> _ChatResponse: ...


def _prompt(name: str) -> str:
    return (files("app.players.prompts") / f"{name}.txt").read_text(encoding="utf-8")


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
    def __init__(self, settings: Settings, client: _ChatClient | None = None) -> None:
        self.s = settings
        # The client is duck-typed (anything exposing .chat(...).message.content); the real
        # ollama.Client has a broader signature than our minimal protocol, so store as Any.
        self.client: Any = client if client is not None else Client(host=settings.ollama_host)

    def _opts(self) -> dict[str, Any]:
        return {
            "temperature": self.s.ollama_temperature,
            "seed": self.s.ollama_seed,
            "num_ctx": self.s.ollama_num_ctx,
            "num_predict": self.s.ollama_num_predict,
        }

    def _ask(
        self,
        template: str,
        schema: type[BaseModel],
        state: str,
        correction: str | None,
        history: list[dict[str, str]],
    ) -> tuple[BaseModel, list[dict[str, str]]]:
        schema_hint = json.dumps(schema.model_json_schema())
        if not history:
            # First turn of the game: send full rules + initial state.
            content = template.replace("{{STATE}}", state).replace("{{SCHEMA}}", schema_hint)
            history = [{"role": "user", "content": content}]
        elif correction:
            # Invalid move: append the specific rejection message so the model self-corrects.
            history = history + [{"role": "user", "content": correction}]
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
        raw = self.client.chat(
            model=self.s.ollama_model,
            messages=history,
            format="json",   # loose JSON mode — MLX models return empty on format=<schema>
            think=False,     # top-level param: stops thinking tokens eating the num_predict budget
            options=self._opts(),
        ).message.content
        clean = _sanitize_json_strings(_strip_code_fence(raw))
        history = history + [{"role": "assistant", "content": raw}]
        return schema.model_validate_json(clean), history  # raises ValidationError on bad JSON

    def play_wordle(self, engine: WordleEngine) -> list[TurnRecord]:
        template = _prompt("wordle")
        turns: list[TurnRecord] = []
        history: list[dict[str, str]] = []  # one conversation for the whole game
        while engine.status is None:
            correction: str | None = None
            retries = 0
            while True:
                if retries > self.s.max_invalid_retries:
                    raise InvalidMoveExhausted("wordle")
                try:
                    turn, history = self._ask(
                        template, WordleTurn, engine.render_state(), correction, history
                    )
                except (ValidationError, json.JSONDecodeError) as e:
                    correction = f"Invalid JSON for the schema: {e}. Reply ONLY with JSON."
                    retries += 1
                    continue
                assert isinstance(turn, WordleTurn)
                problem = engine.validate_guess(turn.guess)
                if problem is None:
                    break
                correction = problem.feedback
                retries += 1
            marks = engine.apply_guess(turn.guess)
            turns.append(
                TurnRecord(
                    len(turns),
                    turn.guess.lower(),
                    "".join(m.value for m in marks),
                    turn.reasoning,
                    retries,
                )
            )
        return turns

    def play_connections(self, engine: ConnectionsEngine) -> list[TurnRecord]:
        template = _prompt("connections")
        turns: list[TurnRecord] = []
        history: list[dict[str, str]] = []  # one conversation for the whole game
        while engine.status is None:
            correction: str | None = None
            retries = 0
            while True:
                if retries > self.s.max_invalid_retries:
                    raise InvalidMoveExhausted("connections")
                try:
                    turn, history = self._ask(
                        template, ConnectionsTurn, engine.render_state(), correction, history
                    )
                except (ValidationError, json.JSONDecodeError) as e:
                    correction = f"Invalid JSON for the schema: {e}. Reply ONLY with JSON."
                    retries += 1
                    continue
                assert isinstance(turn, ConnectionsTurn)
                problem = engine.validate_selection(turn.group)
                if problem is None:
                    break
                correction = problem.feedback
                retries += 1
            result = engine.submit(turn.group)
            turns.append(
                TurnRecord(
                    len(turns),
                    "/".join(turn.group),
                    result.value,
                    turn.reasoning,
                    retries,
                )
            )
        return turns
