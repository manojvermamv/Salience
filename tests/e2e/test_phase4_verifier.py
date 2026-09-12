import os

import pytest

from salience.bootstrap.service import ContentProgramService
from salience.memory.repository import MemoryRepository


@pytest.mark.asyncio
async def test_niche_only_bootstrap_creates_explainable_strategy_and_memory() -> None:
    service = ContentProgramService(database_url=os.environ["TEST_DATABASE_URL"])

    result = await service.create_from_niche(niche="Personal Finance")

    assert result.strategy["content_pillars"]
    assert result.assumptions
    assert result.evidence
    memory = await MemoryRepository(os.environ["TEST_DATABASE_URL"]).retrieve(
        program_id=result.content_program_id, scopes={"semantic", "evidence"}
    )
    assert memory
    assert result.research_agent_run_id
    assert result.strategy_agent_run_id
