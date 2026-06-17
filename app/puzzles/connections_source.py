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
