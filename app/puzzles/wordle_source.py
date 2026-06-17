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
