import os
from uuid import uuid4

import pytest

from salience.memory.contracts import MemoryRecordInput
from salience.memory.repository import MemoryRepository
from salience.workflows.persistence import CanonicalJobStore


@pytest.mark.asyncio
async def test_memory_retrieval_never_returns_another_programs_records() -> None:
    store = CanonicalJobStore(os.environ["TEST_DATABASE_URL"])
    first_workspace = await store.create_workspace(
        slug=f"memory-one-{uuid4().hex}", display_name="Memory one"
    )
    second_workspace = await store.create_workspace(
        slug=f"memory-two-{uuid4().hex}", display_name="Memory two"
    )
    first_program = await store.create_content_program(
        workspace_id=first_workspace.workspace_id,
        slug="program-one",
        name="Program one",
        niche="Finance",
    )
    second_program = await store.create_content_program(
        workspace_id=second_workspace.workspace_id,
        slug="program-two",
        name="Program two",
        niche="Finance",
    )
    repository = MemoryRepository(os.environ["TEST_DATABASE_URL"])

    await repository.record(
        workspace_id=first_workspace.workspace_id,
        program_id=first_program.content_program_id,
        record=MemoryRecordInput(scope="semantic", content={"audience": "A"}),
    )

    assert await repository.retrieve(
        program_id=second_program.content_program_id, scopes={"semantic"}
    ) == []
