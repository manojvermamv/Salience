from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ResearchFinding:
    source_uri: str
    fetched_at: datetime
    content: dict[str, object]
    trust_level: str = "fixture"
    verification_status: str = "verified"


class ResearchConnector(Protocol):
    async def research(self, niche: str) -> list[ResearchFinding]: ...
