from datetime import UTC, datetime

from salience.research.contracts import ResearchFinding


class FixtureResearchConnector:
    """Repeatable local evidence; no browser, account, or network effect."""

    async def research(self, niche: str) -> list[ResearchFinding]:
        normalized = niche.strip().lower().replace(" ", "-")
        return [
            ResearchFinding(
                source_uri=f"fixture://research/{normalized}/audience",
                fetched_at=datetime(2026, 9, 12, tzinfo=UTC),
                content={
                    "niche": niche,
                    "audience": "people seeking practical, trustworthy guidance",
                    "source": "fixture",
                },
                source_identity=f"fixture://research/{normalized}",
                provenance={"connector": "fixture", "source_kind": "fixture"},
            )
        ]
