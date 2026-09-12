import pytest

from salience.research.fixtures import FixtureResearchConnector


@pytest.mark.asyncio
async def test_fixture_research_connector_returns_repeatable_evidence() -> None:
    connector = FixtureResearchConnector()

    first = await connector.research("Personal Finance")
    second = await connector.research("Personal Finance")

    assert first == second
