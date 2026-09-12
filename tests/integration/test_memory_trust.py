import os
from uuid import uuid4

import pytest

from salience.governance.trust import TrustContext
from salience.memory.contracts import MemoryRecordInput
from salience.memory.repository import MemoryRepository
from salience.workflows.persistence import CanonicalJobStore


@pytest.mark.asyncio
async def test_untrusted_research_memory_keeps_source_and_cannot_be_verified_without_evidence() -> None:
    store = CanonicalJobStore(os.environ["TEST_DATABASE_URL"])
    workspace = await store.create_workspace(
        slug=f"trust-memory-{uuid4().hex}", display_name="Trust memory"
    )
    program = await store.create_content_program(
        workspace_id=workspace.workspace_id,
        slug="trust-program",
        name="Trust program",
        niche="Research trust",
    )
    repository = MemoryRepository(os.environ["TEST_DATABASE_URL"])

    record = await repository.record_external_research(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        record=MemoryRecordInput(scope="evidence", content={"text": "external"}),
        context=TrustContext.untrusted_source("https://example.test/article"),
    )

    assert record.trust_level == "untrusted_external"
    assert record.verification_status == "unverified"
    assert record.source_uri == "https://example.test/article"
    assert record.writer_identity == "external-research"
