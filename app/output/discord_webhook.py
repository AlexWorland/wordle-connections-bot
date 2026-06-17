import logging
import time

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

COLOR_WIN = 5763719
COLOR_LOSS = 15548997

MAX_POST_RETRIES = 3


def build_wordle_embed(
    number: int | None,
    marks_rows: list[str],
    solution: str,
    model: str,
    won: bool,
) -> dict:
    grid = "\n".join(marks_rows)
    outcome = "solved" if won else "failed"
    title = f"Wordle #{number}" if number is not None else "Wordle"
    description = f"{grid}\n\nAnswer: ||{solution.upper()}||"
    return {
        "title": f"{title} — {outcome}",
        "description": description,
        "color": COLOR_WIN if won else COLOR_LOSS,
        "footer": {"text": f"Played by {model}"},
    }


def build_connections_embed(
    number: int | None,
    grid: str,
    groups_text: str,
    model: str,
    mistakes: int,
    won: bool,
) -> dict:
    outcome = "solved" if won else "failed"
    title = f"Connections #{number}" if number is not None else "Connections"
    description = f"{grid}\n\nMistakes: {mistakes}\n\n||{groups_text}||"
    return {
        "title": f"{title} — {outcome}",
        "description": description,
        "color": COLOR_WIN if won else COLOR_LOSS,
        "footer": {"text": f"Played by {model}"},
    }


def post_embed(embed: dict, settings: Settings, client: httpx.Client | None = None) -> None:
    owns = client is None
    client = client or httpx.Client(timeout=settings.nyt_timeout_seconds)
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    try:
        for attempt in range(MAX_POST_RETRIES):
            resp = client.post(settings.discord_webhook_url, json=payload)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 1))
                logger.warning(
                    "Discord rate-limited posting to %s; retrying after %ss",
                    settings.redacted_webhook(),
                    retry_after,
                )
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return
        logger.error("Discord post to %s failed after retries", settings.redacted_webhook())
    finally:
        if owns:
            client.close()
