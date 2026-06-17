from datetime import datetime
from zoneinfo import ZoneInfo


def today_str(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
